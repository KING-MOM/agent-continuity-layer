#!/usr/bin/env python3
"""M17.0 transcript smoke — sandbox JSONL fixtures + list/show/path.

Creates a synthetic ~/.claude/projects/ tree under a temp HOME with
known-shape JSONL files, then exercises the transcript CLI against it.
Does not touch the operator's real ~/.claude/projects/.

Checks:
  T1  list with empty projects dir → "(no Claude Code sessions found)"
  T2  list with one valid session → table includes the session
  T3  list --json → valid JSON, sessions array populated
  T4  list --limit 1 from multi-session sandbox → exactly 1 entry
  T5  list --repo <substring> → filters by cwd
  T6  show <full-id> → metadata block with title, cwd, msgs, tools
  T7  show <prefix> → resolves uniquely
  T8  show <ambiguous-prefix> → rc=1 with candidate list
  T9  show <no-match> → rc=1
  T10 path <session> → emits absolute jsonl path
  T11 malformed JSONL lines silently skipped (don't break scan)
  T12 ai-title type recognized → title in output
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TRANSCRIPT_SH = REPO_ROOT / "scripts" / "transcript.sh"


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
            print(f"   FAIL: {e}")
            self.failed.append((name, str(e)))
        except Exception as e:  # noqa: BLE001
            print(f"   FAIL: {type(e).__name__}: {e}")
            self.failed.append((name, f"{type(e).__name__}: {e}"))
        else:
            print("   PASS")
            self.passed.append(name)


def _env_with_home(home: pathlib.Path) -> dict[str, str]:
    e = os.environ.copy()
    e["HOME"] = str(home)
    return e


def _make_session(home: pathlib.Path, encoded_project: str, session_id: str,
                  cwd: str, title: str | None = None,
                  user_msgs: int = 3, assistant_msgs: int = 3,
                  tool_calls: dict[str, int] | None = None,
                  inject_malformed: bool = False) -> pathlib.Path:
    """Create a synthetic Claude Code session JSONL with the requested
    shape under home/.claude/projects/."""
    projects = home / ".claude" / "projects" / encoded_project
    projects.mkdir(parents=True, exist_ok=True)
    jsonl_path = projects / f"{session_id}.jsonl"

    lines: list[str] = []

    if title is not None:
        lines.append(json.dumps({
            "type": "ai-title",
            "sessionId": session_id,
            "aiTitle": title,
        }))

    # Interleave user / assistant messages
    base_ts = "2026-05-28T10:00:00.000Z"
    for i in range(max(user_msgs, assistant_msgs)):
        ts_min = 30 + i  # spread minutes
        ts = f"2026-05-28T10:{ts_min:02d}:00.000Z"
        if i < user_msgs:
            lines.append(json.dumps({
                "type": "user",
                "timestamp": ts,
                "sessionId": session_id,
                "cwd": cwd,
                "gitBranch": "main",
                "version": "2.1.145",
                "message": {"role": "user", "content": [{"type": "text", "text": f"prompt {i}"}]},
            }))
        if i < assistant_msgs:
            # Distribute tool_use blocks into assistant content
            content_blocks: list[dict] = [{"type": "text", "text": f"response {i}"}]
            for tname, n in (tool_calls or {}).items():
                # Spread N tool_use blocks across the assistant messages
                if i < n:
                    content_blocks.append({
                        "type": "tool_use",
                        "name": tname,
                        "input": {},
                    })
            ts_assistant = f"2026-05-28T10:{ts_min:02d}:30.000Z"
            lines.append(json.dumps({
                "type": "assistant",
                "timestamp": ts_assistant,
                "sessionId": session_id,
                "cwd": cwd,
                "gitBranch": "main",
                "version": "2.1.145",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-test",
                    "content": content_blocks,
                },
            }))

    if inject_malformed:
        lines.append("{ this is not valid json")
        lines.append("")  # blank line
        # Real entry after malformed should still be picked up
        lines.append(json.dumps({
            "type": "user",
            "timestamp": "2026-05-28T11:00:00.000Z",
            "sessionId": session_id,
            "cwd": cwd,
            "message": {"role": "user", "content": [{"type": "text", "text": "last"}]},
        }))

    jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonl_path


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(TRANSCRIPT_SH)] + args,
        env=env, capture_output=True, text=True,
    )


# ──────────────────────────────────────────────────────────────────
# Tests

def check_empty_list(home: pathlib.Path) -> None:
    env = _env_with_home(home)
    p = _run(["list"], env)
    if p.returncode != 0:
        raise SmokeError(f"list (empty) failed rc={p.returncode}: {p.stderr}")
    if "no Claude Code sessions" not in p.stdout:
        raise SmokeError(f"expected empty-state message, got: {p.stdout!r}")


def check_list_with_session(home: pathlib.Path) -> None:
    _make_session(
        home,
        encoded_project="-tmp-test-repo",
        session_id="abc12345-1111-2222-3333-444455556666",
        cwd="/tmp/test/repo",
        title="Smoke fixture session",
        user_msgs=5, assistant_msgs=5,
        tool_calls={"Bash": 3, "Edit": 2},
    )
    env = _env_with_home(home)
    p = _run(["list"], env)
    if p.returncode != 0:
        raise SmokeError(f"list failed rc={p.returncode}")
    if "abc12345" not in p.stdout:
        raise SmokeError(f"session id not in list output: {p.stdout!r}")
    if "Smoke fixture session" not in p.stdout:
        raise SmokeError(f"ai-title not in list output: {p.stdout!r}")


def check_list_json(home: pathlib.Path) -> None:
    env = _env_with_home(home)
    p = _run(["list", "--json"], env)
    if p.returncode != 0:
        raise SmokeError(f"list --json failed: {p.stderr}")
    data = json.loads(p.stdout)
    if "sessions" not in data or not isinstance(data["sessions"], list):
        raise SmokeError(f"list --json shape unexpected: {p.stdout!r}")
    if not data["sessions"]:
        raise SmokeError("list --json had empty sessions array")
    s = data["sessions"][0]
    for required in ("session_id", "started_at", "message_count", "tool_call_counts"):
        if required not in s:
            raise SmokeError(f"session missing required field {required!r}")


def check_list_limit(home: pathlib.Path) -> None:
    # Add a second session, then list --limit 1
    _make_session(
        home,
        encoded_project="-tmp-other",
        session_id="bbb22222-1111-2222-3333-444455556666",
        cwd="/tmp/other",
        title="Other session",
        user_msgs=2, assistant_msgs=2,
    )
    env = _env_with_home(home)
    p = _run(["list", "--limit", "1", "--json"], env)
    if p.returncode != 0:
        raise SmokeError(f"list --limit failed: {p.stderr}")
    data = json.loads(p.stdout)
    if len(data["sessions"]) != 1:
        raise SmokeError(f"--limit 1 returned {len(data['sessions'])} sessions")


def check_list_repo_filter(home: pathlib.Path) -> None:
    env = _env_with_home(home)
    p = _run(["list", "--repo", "/tmp/test", "--json"], env)
    if p.returncode != 0:
        raise SmokeError(f"list --repo failed: {p.stderr}")
    data = json.loads(p.stdout)
    if len(data["sessions"]) != 1:
        raise SmokeError(f"--repo /tmp/test matched {len(data['sessions'])} sessions; expected 1")
    s = data["sessions"][0]
    if "/tmp/test" not in (s.get("cwd") or ""):
        raise SmokeError(f"filtered session has wrong cwd: {s.get('cwd')!r}")


def check_show_full_id(home: pathlib.Path) -> None:
    env = _env_with_home(home)
    p = _run(["show", "abc12345-1111-2222-3333-444455556666"], env)
    if p.returncode != 0:
        raise SmokeError(f"show failed rc={p.returncode}: {p.stderr}")
    for needle in ("session:", "title:", "messages:", "tool calls:"):
        if needle not in p.stdout:
            raise SmokeError(f"show output missing {needle!r}: {p.stdout!r}")


def check_show_prefix(home: pathlib.Path) -> None:
    env = _env_with_home(home)
    p = _run(["show", "abc12345"], env)
    if p.returncode != 0:
        raise SmokeError(f"show by prefix failed rc={p.returncode}: {p.stderr}")
    if "abc12345-1111-2222-3333-444455556666" not in p.stdout:
        raise SmokeError("prefix didn't resolve to the right full id")


def check_show_ambiguous(home: pathlib.Path) -> None:
    # Add a session with a prefix that overlaps "a"
    _make_session(
        home,
        encoded_project="-tmp-amb",
        session_id="abc99999-1111-2222-3333-444455556666",
        cwd="/tmp/amb",
        user_msgs=1, assistant_msgs=1,
    )
    env = _env_with_home(home)
    p = _run(["show", "abc"], env)
    if p.returncode == 0:
        raise SmokeError(f"expected ambiguous prefix to error, but rc=0")


def check_show_no_match(home: pathlib.Path) -> None:
    env = _env_with_home(home)
    p = _run(["show", "zzzzzzzz"], env)
    if p.returncode == 0:
        raise SmokeError("expected no-match prefix to error, but rc=0")


def check_path(home: pathlib.Path) -> None:
    env = _env_with_home(home)
    p = _run(["path", "abc12345-1111-2222-3333-444455556666"], env)
    if p.returncode != 0:
        raise SmokeError(f"path failed rc={p.returncode}: {p.stderr}")
    emitted = p.stdout.strip()
    if not emitted.endswith(".jsonl"):
        raise SmokeError(f"path didn't emit a .jsonl: {emitted!r}")
    if not pathlib.Path(emitted).is_file():
        raise SmokeError(f"path emitted but file doesn't exist: {emitted}")


def check_malformed_jsonl_skipped(home: pathlib.Path) -> None:
    _make_session(
        home,
        encoded_project="-tmp-mal",
        session_id="malf0000-1111-2222-3333-444455556666",
        cwd="/tmp/mal",
        title="Malformed-tolerant session",
        user_msgs=2, assistant_msgs=2,
        inject_malformed=True,
    )
    env = _env_with_home(home)
    p = _run(["show", "malf0000"], env)
    if p.returncode != 0:
        raise SmokeError(f"show failed on malformed-tolerant session: {p.stderr}")
    if "Malformed-tolerant session" not in p.stdout:
        raise SmokeError("title not extracted from malformed-but-recoverable JSONL")


def check_ai_title_recognized(home: pathlib.Path) -> None:
    # We've already populated sessions with ai-title; verify the JSON
    # path explicitly preserves it.
    env = _env_with_home(home)
    p = _run(["show", "abc12345-1111", "--json"], env)
    if p.returncode != 0:
        raise SmokeError(f"show --json failed: {p.stderr}")
    meta = json.loads(p.stdout)
    if meta.get("ai_title") != "Smoke fixture session":
        raise SmokeError(f"ai_title not 'Smoke fixture session': {meta.get('ai_title')!r}")


# ──────────────────────────────────────────────────────────────────
# Main

def main() -> int:
    if not TRANSCRIPT_SH.exists():
        print(f"error: {TRANSCRIPT_SH} not found", file=sys.stderr)
        return 1

    home = pathlib.Path(tempfile.mkdtemp(prefix="m17-transcript-smoke."))
    print(f"sandbox: {home}\n")
    runner = _Runner()
    try:
        runner.check("T1: list (empty projects dir)", lambda: check_empty_list(home))
        runner.check("T2: list with one session shows title + id", lambda: check_list_with_session(home))
        runner.check("T3: list --json shape valid", lambda: check_list_json(home))
        runner.check("T4: list --limit 1 returns one", lambda: check_list_limit(home))
        runner.check("T5: list --repo substring filters by cwd", lambda: check_list_repo_filter(home))
        runner.check("T6: show <full-id> emits structured metadata", lambda: check_show_full_id(home))
        runner.check("T7: show <prefix> resolves uniquely", lambda: check_show_prefix(home))
        runner.check("T8: show <ambiguous-prefix> errors", lambda: check_show_ambiguous(home))
        runner.check("T9: show <no-match> errors", lambda: check_show_no_match(home))
        runner.check("T10: path emits absolute jsonl path", lambda: check_path(home))
        runner.check("T11: malformed JSONL lines silently skipped", lambda: check_malformed_jsonl_skipped(home))
        runner.check("T12: ai-title preserved in --json output", lambda: check_ai_title_recognized(home))
    finally:
        if not runner.failed:
            shutil.rmtree(home, ignore_errors=True)

    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"transcript smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, msg in runner.failed:
        print(f"  FAIL  {name}  —  {msg}")
    if runner.failed:
        print(f"  sandbox preserved: {home}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
