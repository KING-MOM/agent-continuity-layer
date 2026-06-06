#!/usr/bin/env python3
"""_watch_smoke.py — M9.4 smoke for the opt-in agent-home watcher.

Strategy: replace `agent-continuity` with a fake shim (selected via the
AGENT_CONTINUITY_BIN env var) that:
  - emits canned `connect doctor --json` output controlled by a state file
  - records each `connect <target> --apply` invocation to a log file

Then drive _watch.py's --tick, enable, disable, status paths and assert
that the right doctor/apply calls happen + state updates are correct.

What we CAN'T smoke from Python (and don't try to):
  - actual launchctl load/unload behavior — that's an OS interaction
  - fsevents wake firing — purely OS
  - macOS TCC permission prompts
For those, only manual install on a real machine surfaces issues.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WATCH_PY = REPO_ROOT / "scripts" / "_watch.py"


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
        except Exception as e:
            self.failed.append((name, f"unexpected: {type(e).__name__}: {e}"))
            print(f"   FAIL (unexpected): {type(e).__name__}: {e}")
        else:
            self.passed.append(name)
            print(f"   PASS")


def _make_fake_shim(sandbox: pathlib.Path) -> pathlib.Path:
    """Write a small Python shim that pretends to be agent-continuity.

    Reads canned doctor output from $FAKE_DOCTOR_JSON (a file path).
    Logs every `connect <target> --apply` call to $FAKE_APPLY_LOG.
    """
    shim = sandbox / "fake-agent-continuity"
    body = """#!/usr/bin/env python3
import json, os, sys, pathlib
args = sys.argv[1:]
if args[:2] == ["connect", "doctor"]:
    canned = os.environ.get("FAKE_DOCTOR_JSON", "")
    if canned and pathlib.Path(canned).exists():
        payload = pathlib.Path(canned).read_text()
    else:
        payload = json.dumps({"entries": [], "summary": {}})
    sys.stdout.write(payload)
    # rc=2 means dry-run with pending writes per the connect.sh convention
    data = json.loads(payload)
    pending = sum(
        1 for e in data.get("entries", [])
        if e.get("state") in ("missing-section","missing-entry","wrong-entry","pending-write")
    )
    sys.exit(2 if pending > 0 else 0)
if len(args) >= 3 and args[0] == "connect" and args[2] == "--apply":
    target = args[1]
    log = os.environ.get("FAKE_APPLY_LOG", "")
    if log:
        with open(log, "a") as f:
            f.write(target + "\\n")
    sys.exit(0)
sys.stderr.write(f"fake-shim: unsupported args: {args}\\n")
sys.exit(1)
"""
    shim.write_text(body, encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _write_canned_doctor(path: pathlib.Path, drift_targets: list[str]) -> None:
    entries = []
    for t in drift_targets:
        entries.append({
            "kind": "mcp-config" if t.endswith("-cli") or t in ("claude-desktop","cursor","zed") else "skill",
            "name": t,
            "label": t,
            "state": "missing-section",
        })
    # Add one connected entry to verify it's NOT included in drift
    entries.append({
        "kind": "mcp-config",
        "name": "claude-desktop",
        "label": "Claude Desktop",
        "state": "connected",
    })
    payload = {"entries": entries, "summary": {}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _watch(args: list[str], env: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WATCH_PY), *args],
        env=env,
        capture_output=True,
        text=True,
        input=stdin,
        timeout=30,
    )


def _new_env(sandbox: pathlib.Path, shim: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(sandbox / "home")
    (sandbox / "home").mkdir(exist_ok=True)
    env["XDG_STATE_HOME"] = str(sandbox / "state")
    env["XDG_CACHE_HOME"] = str(sandbox / "cache")
    env["AGENT_CONTINUITY_BIN"] = str(shim)
    env["FAKE_DOCTOR_JSON"] = str(sandbox / "fake-doctor.json")
    env["FAKE_APPLY_LOG"] = str(sandbox / "fake-apply.log")
    return env


# ──────────────────────────────────────────────────────────────────
# Tests

def check_help(env: dict[str, str]) -> None:
    p = _watch(["--help"], env)
    if p.returncode != 0:
        raise SmokeError(f"--help rc={p.returncode} stderr={p.stderr}")
    if "enable" not in p.stdout or "disable" not in p.stdout or "status" not in p.stdout:
        raise SmokeError(f"--help missing subcommands: {p.stdout!r}")


def check_status_disabled_clean(env: dict[str, str]) -> None:
    p = _watch(["status"], env)
    if p.returncode != 0:
        raise SmokeError(f"status rc={p.returncode} stderr={p.stderr}")
    if "disabled" not in p.stdout:
        raise SmokeError(f"status should report disabled: {p.stdout!r}")


def check_status_json_shape(env: dict[str, str]) -> None:
    p = _watch(["status", "--json"], env)
    if p.returncode != 0:
        raise SmokeError(f"status --json rc={p.returncode}")
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise SmokeError(f"status --json not parseable: {e}: {p.stdout!r}")
    required = {"platform","launchagent_path","launchagent_present","log_path",
                "state_path","launchctl_loaded","total_apply_ok","total_apply_fail"}
    missing = required - set(data.keys())
    if missing:
        raise SmokeError(f"status --json missing keys: {missing}")


def check_tick_no_drift(sandbox: pathlib.Path, env: dict[str, str]) -> None:
    """When doctor reports no drift, --tick is a quiet no-op."""
    _write_canned_doctor(pathlib.Path(env["FAKE_DOCTOR_JSON"]), drift_targets=[])
    apply_log = pathlib.Path(env["FAKE_APPLY_LOG"])
    if apply_log.exists():
        apply_log.unlink()
    p = _watch(["--tick"], env)
    if p.returncode != 0:
        raise SmokeError(f"--tick rc={p.returncode} stderr={p.stderr}")
    if apply_log.exists():
        raise SmokeError(f"--tick called apply but should not have: {apply_log.read_text()!r}")


def check_tick_with_drift(sandbox: pathlib.Path, env: dict[str, str]) -> None:
    """When doctor reports drift, --tick calls apply for each drifted target."""
    _write_canned_doctor(pathlib.Path(env["FAKE_DOCTOR_JSON"]), drift_targets=["codex", "gemini-cli"])
    apply_log = pathlib.Path(env["FAKE_APPLY_LOG"])
    if apply_log.exists():
        apply_log.unlink()
    # Clear watcher state so debounce doesn't fire
    state_path = pathlib.Path(env["XDG_STATE_HOME"]) / "agent-continuity" / "watcher.state.json"
    if state_path.exists():
        state_path.unlink()
    p = _watch(["--tick"], env)
    if p.returncode != 0:
        raise SmokeError(f"--tick rc={p.returncode} stderr={p.stderr}")
    if not apply_log.exists():
        raise SmokeError("--tick failed to call apply")
    targets = apply_log.read_text().splitlines()
    if set(targets) != {"codex", "gemini-cli"}:
        raise SmokeError(f"--tick applied wrong targets: {targets}")


def check_tick_debounce(sandbox: pathlib.Path, env: dict[str, str]) -> None:
    """Second --tick within DEBOUNCE_SECONDS should be a no-op."""
    _write_canned_doctor(pathlib.Path(env["FAKE_DOCTOR_JSON"]), drift_targets=["codex"])
    apply_log = pathlib.Path(env["FAKE_APPLY_LOG"])
    if apply_log.exists():
        apply_log.unlink()
    state_path = pathlib.Path(env["XDG_STATE_HOME"]) / "agent-continuity" / "watcher.state.json"
    if state_path.exists():
        state_path.unlink()

    # First tick: should apply
    p1 = _watch(["--tick"], env)
    if p1.returncode != 0:
        raise SmokeError(f"first --tick rc={p1.returncode}")
    count_after_first = len(apply_log.read_text().splitlines()) if apply_log.exists() else 0
    if count_after_first != 1:
        raise SmokeError(f"first --tick should apply 1, got {count_after_first}")

    # Second tick immediately after: should be debounced (no new apply)
    p2 = _watch(["--tick"], env)
    if p2.returncode != 0:
        raise SmokeError(f"second --tick rc={p2.returncode}")
    count_after_second = len(apply_log.read_text().splitlines()) if apply_log.exists() else 0
    if count_after_second != 1:
        raise SmokeError(f"second --tick should be debounced; expected 1 total apply, got {count_after_second}")


def check_tick_audit_in_state(sandbox: pathlib.Path, env: dict[str, str]) -> None:
    """After an apply, the watcher state file should reflect counters."""
    _write_canned_doctor(pathlib.Path(env["FAKE_DOCTOR_JSON"]), drift_targets=["codex"])
    apply_log = pathlib.Path(env["FAKE_APPLY_LOG"])
    if apply_log.exists():
        apply_log.unlink()
    state_path = pathlib.Path(env["XDG_STATE_HOME"]) / "agent-continuity" / "watcher.state.json"
    if state_path.exists():
        state_path.unlink()
    p = _watch(["--tick"], env)
    if p.returncode != 0:
        raise SmokeError(f"--tick rc={p.returncode}")
    if not state_path.exists():
        raise SmokeError("watcher state file not written")
    state = json.loads(state_path.read_text())
    if state.get("last_apply_ok") != 1:
        raise SmokeError(f"state.last_apply_ok != 1: {state}")
    if state.get("total_apply_ok") != 1:
        raise SmokeError(f"state.total_apply_ok != 1: {state}")
    if not state.get("last_tick_iso"):
        raise SmokeError(f"state.last_tick_iso missing: {state}")


def check_disable_when_not_present(env: dict[str, str]) -> None:
    """Disable should be idempotent — no-op if no plist exists."""
    # Make sure the plist path doesn't exist in the sandbox HOME
    p = _watch(["disable"], env)
    if p.returncode != 0:
        raise SmokeError(f"disable when absent rc={p.returncode}")


def check_enable_force_writes_plist(sandbox: pathlib.Path, env: dict[str, str]) -> None:
    """--force makes enable write the plist on non-Darwin (and Darwin without
    actually loading via launchctl in our sandbox). We can only test that
    the plist gets written and is valid XML."""
    # Build a fake shim that exists at a real path so the existence check passes
    # The shim already exists per env setup. Force enable.
    # On Darwin this WILL try to launchctl-load; we capture stderr to surface.
    p = _watch(["enable", "--force"], env)
    plist = pathlib.Path(env["HOME"]) / "Library" / "LaunchAgents" / "com.agent-continuity.watcher.plist"
    if not plist.exists():
        # Enable may have failed at launchctl step; check return + plist existence
        raise SmokeError(f"plist not written at {plist}; rc={p.returncode} stderr={p.stderr}")
    body = plist.read_text()
    if "com.agent-continuity.watcher" not in body:
        raise SmokeError(f"plist missing label: {body[:200]}")
    if "<key>WatchPaths</key>" not in body:
        raise SmokeError(f"plist missing WatchPaths: {body[:300]}")
    if "<key>ThrottleInterval</key>" not in body:
        raise SmokeError(f"plist missing ThrottleInterval: {body[:300]}")
    # Validate it's parseable XML
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(body)
    except ET.ParseError as e:
        raise SmokeError(f"plist not valid XML: {e}")


# ──────────────────────────────────────────────────────────────────

def main() -> int:
    sandbox = pathlib.Path(tempfile.mkdtemp(prefix="watch-smoke."))
    print(f"sandbox: {sandbox}")
    shim = _make_fake_shim(sandbox)
    env = _new_env(sandbox, shim)

    runner = _Runner()
    try:
        runner.check("watch --help works", lambda: check_help(env))
        runner.check("status reports disabled cleanly", lambda: check_status_disabled_clean(env))
        runner.check("status --json shape OK", lambda: check_status_json_shape(env))
        runner.check("--tick no-op when no drift", lambda: check_tick_no_drift(sandbox, env))
        runner.check("--tick applies drift targets", lambda: check_tick_with_drift(sandbox, env))
        runner.check("--tick debounces within window", lambda: check_tick_debounce(sandbox, env))
        runner.check("--tick records audit in state file", lambda: check_tick_audit_in_state(sandbox, env))
        runner.check("disable is idempotent when absent", lambda: check_disable_when_not_present(env))
        runner.check("enable --force writes valid plist", lambda: check_enable_force_writes_plist(sandbox, env))
    finally:
        if not runner.failed:
            shutil.rmtree(sandbox, ignore_errors=True)

    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"watch smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, err in runner.failed:
        print(f"  FAIL  {name}: {err}")
    return 0 if not runner.failed else 1


if __name__ == "__main__":
    sys.exit(main())
