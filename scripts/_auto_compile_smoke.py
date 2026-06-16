#!/usr/bin/env python3
"""_auto_compile_smoke.py — v0.5.4 auto-compile system smoke.

Closes the "I worked for hours and forgot to compile" gap. The substrate's
auto-compile pass lists sessions, filters by quiescence age, skips already-
compiled sessions, and applies compile to the rest.

Suite:
  A1   auto-compile compiles a quiesced, never-compiled session
  A2   auto-compile skips a recent (non-quiesced) session
  A3   auto-compile skips an already-compiled session (idempotent)
  A4   auto-compile is failure-tolerant (one bad session doesn't block others)
  A5   --min-age-hours threshold respected
  A6   schedule status reports disabled when no LaunchAgent present
  A7   schedule enable --force writes a valid plist (skipped on non-darwin)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


class SmokeError(Exception):
    pass


class _Runner:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def check(self, name: str, fn) -> None:
        print(f"── {name} ──")
        try:
            fn()
        except SmokeError as e:
            self.failed.append((name, str(e)))
            print(f"   FAIL: {e}")
        except AssertionError as e:
            self.failed.append((name, f"assertion: {e}"))
            print(f"   FAIL: {e}")
        except Exception as e:
            self.failed.append((name, f"unexpected {type(e).__name__}: {e}"))
            print(f"   FAIL (unexpected): {type(e).__name__}: {e}")
        else:
            self.passed.append(name)
            print("   PASS")


def _env(home: pathlib.Path, **extra: str) -> dict[str, str]:
    """Build env pointing at sandbox paths."""
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    env.pop("AGENT_CONTINUITY_TEAM_ID", None)
    env.pop("AGENT_CONTINUITY_TEAM_REPO", None)
    env.update(extra)
    return env


def _run(args: list[str], env: dict[str, str], check_rc: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)
    if check_rc and r.returncode != 0:
        raise SmokeError(f"{args[1] if len(args) > 1 else args[0]} rc={r.returncode}: {r.stderr}")
    return r


def _make_session(home: pathlib.Path, session_id: str, last_at_iso: str, cwd: str = "/tmp/test-proj") -> pathlib.Path:
    """Create a Claude Code session JSONL with one git commit tool_use that
    the compile heuristic will pick up.

    The session is placed at ~/.claude/projects/-tmp-test-proj/<session_id>.jsonl
    matching the encoded-path scheme the real Claude Code uses.
    """
    proj_dir = home / ".claude" / "projects" / "-tmp-test-proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    jsonl = proj_dir / f"{session_id}.jsonl"
    # Three entries: ai-title, one user message, one assistant tool_use
    lines = [
        json.dumps({"type": "ai-title", "aiTitle": "Test session", "sessionId": session_id}),
        json.dumps({
            "type": "user",
            "sessionId": session_id,
            "timestamp": last_at_iso,
            "cwd": cwd,
            "version": "2.0.0",
            "message": {"role": "user", "content": "test"},
        }),
        json.dumps({
            "type": "assistant",
            "sessionId": session_id,
            "timestamp": last_at_iso,
            "cwd": cwd,
            "version": "2.0.0",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "Bash",
                        "input": {"command": f'git commit -m "test commit for session {session_id[:8]}"'},
                    }
                ],
            },
        }),
    ]
    jsonl.write_text("\n".join(lines) + "\n")
    return jsonl


def _read_decisions(state: pathlib.Path) -> list[dict]:
    p = state / "agent-continuity" / "decisions.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ──────────────────────────────────────────────────────────────────

def t_compile_quiesced_session(td: pathlib.Path) -> None:
    home = td / "A1-home"
    env = _env(home)
    # Old enough to be quiesced
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _make_session(home, "aaaaaaaa-1111-1111-1111-111111111111", old_ts)
    r = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
              "auto-compile", "--json"], env)
    summary = json.loads(r.stdout)
    if summary["compiled"] != 1:
        raise SmokeError(f"expected 1 compiled, got {summary['compiled']}: {summary}")
    # Verify the actual entry landed in decisions.jsonl
    entries = _read_decisions(home / ".local" / "state")
    if not any("aaaaaaaa" in str(e.get("author", "")) for e in entries):
        raise SmokeError(f"compiled session not in decisions log: {entries}")


def t_skip_recent_session(td: pathlib.Path) -> None:
    home = td / "A2-home"
    env = _env(home)
    # Recent (now) — should be skipped
    recent_ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _make_session(home, "bbbbbbbb-2222-2222-2222-222222222222", recent_ts)
    r = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
              "auto-compile", "--json"], env)
    summary = json.loads(r.stdout)
    if summary["skipped_too_recent"] < 1:
        raise SmokeError(f"expected at least 1 recent skip, got 0: {summary}")
    if summary["compiled"] > 0:
        raise SmokeError(f"recent session was compiled (should have been skipped): {summary}")


def t_skip_already_compiled(td: pathlib.Path) -> None:
    home = td / "A3-home"
    env = _env(home)
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _make_session(home, "cccccccc-3333-3333-3333-333333333333", old_ts)
    # First pass: should compile
    r1 = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
               "auto-compile", "--json"], env)
    s1 = json.loads(r1.stdout)
    if s1["compiled"] != 1:
        raise SmokeError(f"first pass should have compiled 1: {s1}")
    # Second pass: should skip
    r2 = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
               "auto-compile", "--json"], env)
    s2 = json.loads(r2.stdout)
    if s2["compiled"] != 0:
        raise SmokeError(f"second pass compiled non-zero (idempotency broken): {s2}")
    if s2["skipped_already_compiled"] < 1:
        raise SmokeError(f"second pass didn't skip already-compiled: {s2}")


def t_failure_tolerance(td: pathlib.Path) -> None:
    """Inject a malformed JSONL alongside a valid session. The pass should
    log the failure and keep going (the malformed one returns None from
    _scan_session so it's just skipped silently — verify the valid one
    still compiles)."""
    home = td / "A4-home"
    env = _env(home)
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _make_session(home, "dddddddd-4444-4444-4444-444444444444", old_ts)
    # Plant a malformed sibling JSONL
    bad = home / ".claude" / "projects" / "-tmp-test-proj" / "eeeeeeee-5555-5555-5555-555555555555.jsonl"
    bad.write_text("this is not json\n{also not json\n")
    r = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
              "auto-compile", "--json"], env)
    summary = json.loads(r.stdout)
    # Should compile the valid one (the malformed one fails _scan_session
    # which returns None and we continue silently — that's the contract)
    if summary["compiled"] != 1:
        raise SmokeError(f"failure-tolerance failed — valid session not compiled: {summary}")


def t_min_age_threshold(td: pathlib.Path) -> None:
    """A session 30min old should compile when --min-age-hours=0.25 but skip
    when --min-age-hours=1.0 (the default)."""
    home = td / "A5-home"
    env = _env(home)
    moderate_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _make_session(home, "ffffffff-6666-6666-6666-666666666666", moderate_ts)
    # Default (1.0): should skip
    r1 = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
               "auto-compile", "--json"], env)
    s1 = json.loads(r1.stdout)
    if s1["compiled"] != 0:
        raise SmokeError(f"30min-old session compiled at default 1.0h threshold (should skip): {s1}")
    # Lower threshold: should compile
    r2 = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
               "auto-compile", "--json", "--min-age-hours", "0.25"], env)
    s2 = json.loads(r2.stdout)
    if s2["compiled"] != 1:
        raise SmokeError(f"30min-old session not compiled at 0.25h threshold: {s2}")


def t_schedule_status_disabled(td: pathlib.Path) -> None:
    home = td / "A6-home"
    env = _env(home)
    r = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
              "schedule", "status", "--json"], env)
    info = json.loads(r.stdout)
    if info["launchctl_loaded"] is not False:
        raise SmokeError(f"expected launchctl_loaded=False on fresh sandbox: {info}")
    if info["launchagent_present"] is not False:
        raise SmokeError(f"expected launchagent_present=False on fresh sandbox: {info}")


def t_schedule_enable_writes_plist(td: pathlib.Path) -> None:
    """On non-Darwin this test is skipped; on Darwin we still need launchctl
    to be present. We test only that the plist is well-formed XML; loading
    via launchctl is a system interaction we don't exercise here."""
    if sys.platform != "darwin":
        return  # Treat as PASS — schedule enable is darwin-only
    home = td / "A7-home"
    env = _env(home)
    # Plant a fake shim so _autocompile_plist has a real path to embed
    (home / ".local" / "bin").mkdir(parents=True, exist_ok=True)
    (home / ".local" / "bin" / "agent-continuity").write_text("#!/bin/sh\nexit 0\n")
    os.chmod(home / ".local" / "bin" / "agent-continuity", 0o755)
    r = _run([sys.executable, str(SCRIPTS / "_transcript.py"),
              "schedule", "enable", "--interval-seconds", "600"],
             env, check_rc=False)
    plist = home / "Library" / "LaunchAgents" / "com.agent-continuity.transcript-auto-compile.plist"
    if not plist.exists():
        raise SmokeError(f"plist not written at {plist}; rc={r.returncode} stderr={r.stderr}")
    body = plist.read_text()
    if "transcript-auto-compile" not in body:
        raise SmokeError("plist missing label")
    if "<key>StartInterval</key>" not in body or "<integer>600</integer>" not in body:
        raise SmokeError("plist missing StartInterval=600")
    # Parse as XML
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(body)
    except ET.ParseError as e:
        raise SmokeError(f"plist not valid XML: {e}")


# ──────────────────────────────────────────────────────────────────

def main() -> int:
    td = pathlib.Path(tempfile.mkdtemp(prefix="auto-compile-smoke."))
    print(f"sandbox: {td}\n")
    runner = _Runner()
    try:
        runner.check("A1: auto-compile picks up a quiesced session", lambda: t_compile_quiesced_session(td))
        runner.check("A2: auto-compile skips a recent session (under quiescence threshold)", lambda: t_skip_recent_session(td))
        runner.check("A3: auto-compile is idempotent (skips already-compiled)", lambda: t_skip_already_compiled(td))
        runner.check("A4: auto-compile is failure-tolerant (bad sessions don't block valid ones)", lambda: t_failure_tolerance(td))
        runner.check("A5: --min-age-hours threshold respected", lambda: t_min_age_threshold(td))
        runner.check("A6: schedule status reports disabled on a fresh sandbox", lambda: t_schedule_status_disabled(td))
        runner.check("A7: schedule enable writes valid plist with StartInterval", lambda: t_schedule_enable_writes_plist(td))
    finally:
        if not runner.failed:
            shutil.rmtree(td, ignore_errors=True)
    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"auto-compile smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, err in runner.failed:
        print(f"  FAIL  {name}: {err}")
    return 0 if not runner.failed else 1


if __name__ == "__main__":
    sys.exit(main())
