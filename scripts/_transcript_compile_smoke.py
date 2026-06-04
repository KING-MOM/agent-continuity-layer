#!/usr/bin/env python3
"""M17.1 transcript-compile smoke — synthetic JSONL fixtures exercising
each heuristic + privacy denylist + idempotency.

Sandbox a temp $XDG_STATE_HOME so the smoke writes to its own
decisions.jsonl, not the operator's real one. Build synthetic JSONLs
with controlled tool_use blocks and verify the compile extractor
emits the expected events with the expected privacy filtering.

Checks:
  T1  git commit Bash → emits git-commit candidate
  T2  github release Bash → emits github-release candidate
  T3  git tag Bash → emits git-tag candidate
  T4  package install Bash → emits package-install candidate
  T5  Edit to load-bearing path → emits file-edit candidate
  T6  Edit to OUT-OF-cwd path → skipped (no candidate)
  T7  Edit to non-load-bearing path → skipped
  T8  AskUserQuestion → one candidate per question.header
  T9  explicit decisions add Bash → emits operator-explicit candidate
  T10 sensitive file_path (credentials/openai.json) → skipped_sensitive ≥ 1
  T11 sensitive Bash command (contains sk-) → skipped_sensitive ≥ 1
  T12 dry-run does NOT write decisions; --apply does
  T13 idempotency: --apply twice writes zero new entries the second time
  T14 compiled entries land with author='auto:transcript-compile@<prefix>'
      and refs include 'session:<id>'
  T15 --no-privacy-filter bypasses denylist
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
    # Isolate decisions.jsonl writes from the operator's real log
    e["XDG_STATE_HOME"] = str(home / ".local/state")
    return e


def _build_jsonl(
    home: pathlib.Path,
    session_id: str,
    cwd: str,
    encoded: str,
    tool_calls: list[dict],
) -> pathlib.Path:
    """Create a JSONL with one assistant message per tool_call. Each
    tool_calls element is a dict {name, input, ts}."""
    projects = home / ".claude" / "projects" / encoded
    projects.mkdir(parents=True, exist_ok=True)
    jsonl_path = projects / f"{session_id}.jsonl"
    lines = []
    # User message to anchor cwd
    lines.append(json.dumps({
        "type": "user",
        "timestamp": "2026-05-28T10:00:00.000Z",
        "sessionId": session_id,
        "cwd": cwd,
        "message": {"role": "user", "content": [{"type": "text", "text": "kickoff"}]},
    }))
    for i, tc in enumerate(tool_calls):
        ts = tc.get("ts", f"2026-05-28T10:{30 + i:02d}:00.000Z")
        lines.append(json.dumps({
            "type": "assistant",
            "timestamp": ts,
            "sessionId": session_id,
            "cwd": cwd,
            "message": {
                "role": "assistant",
                "model": "claude-opus-test",
                "content": [
                    {"type": "text", "text": "doing the thing"},
                    {
                        "type": "tool_use",
                        "name": tc["name"],
                        "input": tc["input"],
                        "id": f"tc_{i}",
                    },
                ],
            },
        }))
    jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonl_path


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(TRANSCRIPT_SH)] + args,
        env=env, capture_output=True, text=True,
    )


def _compile_json(session_prefix: str, env: dict[str, str], apply: bool = False) -> dict:
    args = ["compile", session_prefix, "--json"]
    if apply:
        args.append("--apply")
    p = _run(args, env)
    if p.returncode != 0:
        raise SmokeError(f"compile failed rc={p.returncode}: {p.stderr}")
    return json.loads(p.stdout)


def _candidates_by_type(result: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in result.get("candidates", []):
        out.setdefault(c["source_type"], []).append(c)
    return out


# ──────────────────────────────────────────────────────────────────
# Tests

def check_git_commit(home, env):
    _build_jsonl(
        home, "11110000-1111-1111-1111-111111111111",
        cwd="/tmp/proj-git",
        encoded="-tmp-proj-git",
        tool_calls=[{
            "name": "Bash",
            "input": {"command": 'git commit -m "test: example commit subject"'},
        }],
    )
    r = _compile_json("11110000", env)
    by = _candidates_by_type(r)
    if "git-commit" not in by:
        raise SmokeError(f"no git-commit candidate: {by.keys()}")
    if "example commit subject" not in by["git-commit"][0]["decision"]:
        raise SmokeError(f"unexpected decision: {by['git-commit'][0]}")


def check_github_release(home, env):
    _build_jsonl(
        home, "22220000-2222-2222-2222-222222222222",
        cwd="/tmp/proj-rel",
        encoded="-tmp-proj-rel",
        tool_calls=[{
            "name": "Bash",
            "input": {"command": "gh release create v0.3.0 dist/file.tar.gz --title hello"},
        }],
    )
    r = _compile_json("22220000", env)
    by = _candidates_by_type(r)
    if "github-release" not in by:
        raise SmokeError(f"no github-release candidate: {by.keys()}")
    if "v0.3.0" not in by["github-release"][0]["decision"]:
        raise SmokeError(f"version not in decision: {by['github-release'][0]}")


def check_git_tag(home, env):
    _build_jsonl(
        home, "33330000-3333-3333-3333-333333333333",
        cwd="/tmp/proj-tag",
        encoded="-tmp-proj-tag",
        tool_calls=[{
            "name": "Bash",
            "input": {"command": "git tag -a v0.4.0 -m 'release'"},
        }],
    )
    r = _compile_json("33330000", env)
    by = _candidates_by_type(r)
    if "git-tag" not in by:
        raise SmokeError(f"no git-tag candidate: {by.keys()}")


def check_package_install(home, env):
    _build_jsonl(
        home, "44440000-4444-4444-4444-444444444444",
        cwd="/tmp/proj-pkg",
        encoded="-tmp-proj-pkg",
        tool_calls=[{
            "name": "Bash",
            "input": {"command": "brew install cosign"},
        }],
    )
    r = _compile_json("44440000", env)
    by = _candidates_by_type(r)
    if "package-install" not in by:
        raise SmokeError(f"no package-install candidate: {by.keys()}")
    if "cosign" not in by["package-install"][0]["decision"]:
        raise SmokeError(f"package not in decision: {by['package-install'][0]}")


def check_file_edit_load_bearing(home, env):
    _build_jsonl(
        home, "55550000-5555-5555-5555-555555555555",
        cwd="/tmp/proj-edit",
        encoded="-tmp-proj-edit",
        tool_calls=[{
            "name": "Edit",
            "input": {"file_path": "/tmp/proj-edit/docs/README.md", "old_string": "x", "new_string": "y"},
        }],
    )
    r = _compile_json("55550000", env)
    by = _candidates_by_type(r)
    if "file-edit" not in by:
        raise SmokeError(f"no file-edit candidate: {by.keys()}")


def check_file_edit_out_of_cwd_skipped(home, env):
    _build_jsonl(
        home, "66660000-6666-6666-6666-666666666666",
        cwd="/tmp/proj-cwd",
        encoded="-tmp-proj-cwd",
        tool_calls=[{
            "name": "Edit",
            "input": {"file_path": "/tmp/SOMEWHERE-ELSE/file.md", "old_string": "x", "new_string": "y"},
        }],
    )
    r = _compile_json("66660000", env)
    if r["candidates"]:
        raise SmokeError(f"expected no candidates for out-of-cwd edit, got: {r['candidates']}")


def check_file_edit_non_load_bearing_skipped(home, env):
    _build_jsonl(
        home, "77770000-7777-7777-7777-777777777777",
        cwd="/tmp/proj-noise",
        encoded="-tmp-proj-noise",
        tool_calls=[{
            "name": "Edit",
            "input": {"file_path": "/tmp/proj-noise/some-random-file.txt", "old_string": "x", "new_string": "y"},
        }],
    )
    r = _compile_json("77770000", env)
    if r["candidates"]:
        raise SmokeError(f"expected no candidates for non-load-bearing edit: {r['candidates']}")


def check_ask_user_question(home, env):
    _build_jsonl(
        home, "88880000-8888-8888-8888-888888888888",
        cwd="/tmp/proj-ask",
        encoded="-tmp-proj-ask",
        tool_calls=[{
            "name": "AskUserQuestion",
            "input": {
                "questions": [
                    {"question": "Which one?", "header": "Pick storage", "multiSelect": False,
                     "options": [{"label": "A", "description": "..."}, {"label": "B", "description": "..."}]},
                    {"question": "Which one too?", "header": "Pick mode", "multiSelect": False,
                     "options": [{"label": "X", "description": "..."}]},
                ],
            },
        }],
    )
    r = _compile_json("88880000", env)
    by = _candidates_by_type(r)
    asks = by.get("ask-user-question", [])
    if len(asks) != 2:
        raise SmokeError(f"expected 2 AskUserQuestion candidates, got {len(asks)}")


def check_explicit_decisions_add(home, env):
    _build_jsonl(
        home, "99990000-9999-9999-9999-999999999999",
        cwd="/tmp/proj-decide",
        encoded="-tmp-proj-decide",
        tool_calls=[{
            "name": "Bash",
            "input": {"command": 'agent-continuity decisions add --adapter human --decision "test decision" --why "test why" --ref repo:x'},
        }],
    )
    r = _compile_json("99990000", env)
    by = _candidates_by_type(r)
    if "explicit-decisions-add" not in by:
        raise SmokeError(f"no explicit-decisions-add candidate: {by.keys()}")
    if "test decision" not in by["explicit-decisions-add"][0]["decision"]:
        raise SmokeError(f"operator decision text not preserved: {by['explicit-decisions-add'][0]}")


def check_sensitive_file_path_skipped(home, env):
    _build_jsonl(
        home, "aaaa0000-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        cwd="/tmp/proj-sens",
        encoded="-tmp-proj-sens",
        tool_calls=[
            # This SHOULD be skipped (credentials/ path)
            {"name": "Edit", "input": {"file_path": "/tmp/proj-sens/credentials/openai.json",
                                         "old_string": "x", "new_string": "y"}},
            # This SHOULD be kept (load-bearing path)
            {"name": "Edit", "input": {"file_path": "/tmp/proj-sens/docs/note.md",
                                         "old_string": "x", "new_string": "y"}},
        ],
    )
    r = _compile_json("aaaa0000", env)
    if r["skipped_sensitive"] < 1:
        raise SmokeError(f"expected skipped_sensitive ≥ 1, got {r['skipped_sensitive']}")


def check_sensitive_bash_command_skipped(home, env):
    _build_jsonl(
        home, "bbbb0000-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        cwd="/tmp/proj-bashsec",
        encoded="-tmp-proj-bashsec",
        tool_calls=[
            # This SHOULD be skipped (contains sk-...)
            {"name": "Bash", "input": {"command": 'export OPENAI_API_KEY=sk-abcdef0123456789abcdef0123456789 && echo go'}},
            # This SHOULD be kept (normal git commit)
            {"name": "Bash", "input": {"command": 'git commit -m "harmless"'}},
        ],
    )
    r = _compile_json("bbbb0000", env)
    if r["skipped_sensitive"] < 1:
        raise SmokeError(f"expected skipped_sensitive ≥ 1, got {r['skipped_sensitive']}")


def check_dry_run_no_write(home, env):
    _build_jsonl(
        home, "cccc0000-cccc-cccc-cccc-cccccccccccc",
        cwd="/tmp/proj-dry",
        encoded="-tmp-proj-dry",
        tool_calls=[{
            "name": "Bash",
            "input": {"command": 'git commit -m "dry-run-test"'},
        }],
    )
    decisions_log = home / ".local/state/agent-continuity/decisions.jsonl"
    size_before = decisions_log.stat().st_size if decisions_log.exists() else 0
    _compile_json("cccc0000", env, apply=False)
    size_after = decisions_log.stat().st_size if decisions_log.exists() else 0
    if size_after != size_before:
        raise SmokeError("dry-run wrote to decisions.jsonl (should be no-op)")


def check_apply_writes_and_idempotent(home, env):
    _build_jsonl(
        home, "dddd0000-dddd-dddd-dddd-dddddddddddd",
        cwd="/tmp/proj-apply",
        encoded="-tmp-proj-apply",
        tool_calls=[{
            "name": "Bash",
            "input": {"command": 'git commit -m "apply-test"'},
        }],
    )
    r1 = _compile_json("dddd0000", env, apply=True)
    if not r1["written"]:
        raise SmokeError("apply did not write any entries")
    r2 = _compile_json("dddd0000", env, apply=True)
    if r2["written"]:
        raise SmokeError(f"second --apply wrote {len(r2['written'])} entries (should be idempotent)")
    if r2["skipped_existing"] < 1:
        raise SmokeError(f"second --apply did not see existing entries: {r2}")


def check_compiled_entry_shape(home, env):
    _build_jsonl(
        home, "eeee0000-eeee-eeee-eeee-eeeeeeeeeeee",
        cwd="/tmp/proj-shape",
        encoded="-tmp-proj-shape",
        tool_calls=[{
            "name": "Bash",
            "input": {"command": 'git commit -m "shape-test"'},
        }],
    )
    _compile_json("eeee0000", env, apply=True)
    decisions_log = home / ".local/state/agent-continuity/decisions.jsonl"
    text = decisions_log.read_text(encoding="utf-8")
    # Find the entry we just added
    found = False
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "shape-test" in entry.get("decision", ""):
            found = True
            if entry.get("author") != "auto:transcript-compile@eeee0000":
                raise SmokeError(f"wrong author: {entry.get('author')}")
            refs = entry.get("refs", [])
            if not any(r.startswith("session:") for r in refs):
                raise SmokeError(f"missing session: ref: {refs}")
            break
    if not found:
        raise SmokeError("written entry not found in decisions.jsonl")


def check_no_privacy_filter_bypass(home, env):
    _build_jsonl(
        home, "ffff0000-ffff-ffff-ffff-ffffffffffff",
        cwd="/tmp/proj-bypass",
        encoded="-tmp-proj-bypass",
        tool_calls=[
            {"name": "Bash", "input": {"command": "echo sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaa"}},
        ],
    )
    # With privacy filter (default): skipped
    r = _compile_json("ffff0000", env)
    if r["skipped_sensitive"] < 1:
        raise SmokeError("expected default to skip sensitive Bash")
    # With --no-privacy-filter: candidate present (no candidate would still
    # be expected because echo isn't a recognized pattern; instead, just
    # check that skipped_sensitive is 0 in bypass mode)
    args = ["compile", "ffff0000", "--json", "--no-privacy-filter"]
    p = _run(args, env)
    if p.returncode != 0:
        raise SmokeError(f"--no-privacy-filter failed: {p.stderr}")
    r2 = json.loads(p.stdout)
    if r2["skipped_sensitive"] != 0:
        raise SmokeError(f"--no-privacy-filter should skip nothing, got {r2['skipped_sensitive']}")


# ──────────────────────────────────────────────────────────────────
# Main

def main() -> int:
    if not TRANSCRIPT_SH.exists():
        print(f"error: {TRANSCRIPT_SH} not found", file=sys.stderr)
        return 1

    home = pathlib.Path(tempfile.mkdtemp(prefix="m17-compile-smoke."))
    print(f"sandbox: {home}\n")
    env = _env_with_home(home)

    runner = _Runner()
    try:
        runner.check("T1: git commit Bash → git-commit candidate", lambda: check_git_commit(home, env))
        runner.check("T2: github release Bash → github-release candidate", lambda: check_github_release(home, env))
        runner.check("T3: git tag Bash → git-tag candidate", lambda: check_git_tag(home, env))
        runner.check("T4: package install Bash → package-install candidate", lambda: check_package_install(home, env))
        runner.check("T5: Edit load-bearing path → file-edit candidate", lambda: check_file_edit_load_bearing(home, env))
        runner.check("T6: Edit out-of-cwd skipped", lambda: check_file_edit_out_of_cwd_skipped(home, env))
        runner.check("T7: Edit non-load-bearing skipped", lambda: check_file_edit_non_load_bearing_skipped(home, env))
        runner.check("T8: AskUserQuestion → 1 candidate per question", lambda: check_ask_user_question(home, env))
        runner.check("T9: explicit decisions add Bash → operator-explicit", lambda: check_explicit_decisions_add(home, env))
        runner.check("T10: sensitive file_path skipped (privacy)", lambda: check_sensitive_file_path_skipped(home, env))
        runner.check("T11: sensitive Bash command skipped (privacy)", lambda: check_sensitive_bash_command_skipped(home, env))
        runner.check("T12: dry-run does NOT write to decisions.jsonl", lambda: check_dry_run_no_write(home, env))
        runner.check("T13: --apply writes; second --apply idempotent", lambda: check_apply_writes_and_idempotent(home, env))
        runner.check("T14: written entry has correct author + session: ref", lambda: check_compiled_entry_shape(home, env))
        runner.check("T15: --no-privacy-filter bypasses denylist", lambda: check_no_privacy_filter_bypass(home, env))
    finally:
        if not runner.failed:
            shutil.rmtree(home, ignore_errors=True)

    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"transcript-compile smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
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
