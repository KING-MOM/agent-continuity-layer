#!/usr/bin/env python3
"""M14.0 smoke for the local project registry.

Covers:
  - CLI: list/add/info/remove + idempotence + lookup variants
  - Auto-register hook fires on decisions add (write-side op)
  - Auto-register no-op when cwd is not a git repo
  - Doctor reports project_registry health correctly across states

Sandboxed via XDG env vars + temp git repos. Does not touch the
operator's real registry.

Exit 0 on full pass; 1 on any failure.
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
PROJECT_SH = REPO_ROOT / "scripts" / "project.sh"
DECISIONS_SH = REPO_ROOT / "scripts" / "decisions.sh"
DOCTOR_SH = REPO_ROOT / "scripts" / "doctor.sh"


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


def _sandbox_env(base: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(base / "home")
    env["XDG_CONFIG_HOME"] = str(base / "config")
    env["XDG_STATE_HOME"] = str(base / "state")
    env["XDG_CACHE_HOME"] = str(base / "cache")
    env["XDG_DATA_HOME"] = str(base / "data")
    return env


def _make_fake_git_repo(parent: pathlib.Path, name: str, origin: str) -> pathlib.Path:
    """Create a minimal git repo with a configured origin remote."""
    repo = parent / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=repo, check=True)
    # Need at least one commit for some git operations to behave
    (repo / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def _run(cmd: list[str], env: dict[str, str], cwd: pathlib.Path | None = None,
         input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        env=env,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        capture_output=True,
        text=True,
    )


def _bootstrap_trust_policy(env: dict[str, str]) -> None:
    """Write a minimal trust policy so decisions.sh add accepts the write."""
    cfg = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "trust-policy.json").write_text(json.dumps({
        "schema_version": "1.0",
        "default": {"allow_kinds": [], "trust_levels": ["low"], "adapters": ["codex", "claude", "human"]},
        "grants": [],
    }))


# ─────────────────────────────────────────────────────────────────────────
# Individual checks

def check_empty_list(env: dict[str, str], sb: pathlib.Path) -> None:
    p = _run([str(PROJECT_SH), "list"], env)
    if p.returncode != 0:
        raise SmokeError(f"list (empty) exited {p.returncode}: {p.stderr}")
    if "no projects" not in p.stdout.lower():
        raise SmokeError(f"expected 'no projects' in stdout, got: {p.stdout!r}")


def check_add_from_git_repo(env: dict[str, str], sb: pathlib.Path) -> None:
    repo = _make_fake_git_repo(sb, "myrepo", "https://example.com/test/myrepo.git")
    p = _run([str(PROJECT_SH), "add"], env, cwd=repo)
    if p.returncode != 0:
        raise SmokeError(f"add exited {p.returncode}: {p.stderr}")
    if "registered:" not in p.stdout:
        raise SmokeError(f"expected 'registered:' in stdout, got: {p.stdout!r}")
    # Confirm file exists
    projects = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity" / "projects"
    files = list(projects.glob("*.json"))
    if len(files) != 1:
        raise SmokeError(f"expected 1 project file, found {len(files)}: {files}")


def check_idempotent_add(env: dict[str, str], sb: pathlib.Path) -> None:
    repo = sb / "myrepo"
    p = _run([str(PROJECT_SH), "add"], env, cwd=repo)
    if p.returncode != 0:
        raise SmokeError(f"second add exited {p.returncode}: {p.stderr}")
    if "already registered" not in p.stdout:
        raise SmokeError(f"expected 'already registered' in stdout, got: {p.stdout!r}")
    projects = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity" / "projects"
    files = list(projects.glob("*.json"))
    if len(files) != 1:
        raise SmokeError(f"add was not idempotent: now {len(files)} files")


def check_list_shows_entry(env: dict[str, str], sb: pathlib.Path) -> None:
    p = _run([str(PROJECT_SH), "list", "--json"], env)
    if p.returncode != 0:
        raise SmokeError(f"list --json exited {p.returncode}: {p.stderr}")
    data = json.loads(p.stdout)
    if not data.get("projects"):
        raise SmokeError(f"projects array empty in list --json: {data}")
    if len(data["projects"]) != 1:
        raise SmokeError(f"expected 1 project in list, got {len(data['projects'])}")


def check_info_by_name(env: dict[str, str], sb: pathlib.Path) -> None:
    p = _run([str(PROJECT_SH), "info", "myrepo"], env)
    if p.returncode != 0:
        raise SmokeError(f"info by name exited {p.returncode}: {p.stderr}")
    data = json.loads(p.stdout)
    if data.get("name") != "myrepo":
        raise SmokeError(f"info returned wrong entry: {data}")


def check_info_by_uuid(env: dict[str, str], sb: pathlib.Path) -> None:
    # Find the UUID from list
    p_list = _run([str(PROJECT_SH), "list", "--json"], env)
    uuid = json.loads(p_list.stdout)["projects"][0]["uuid"]
    p = _run([str(PROJECT_SH), "info", uuid], env)
    if p.returncode != 0:
        raise SmokeError(f"info by uuid exited {p.returncode}: {p.stderr}")
    data = json.loads(p.stdout)
    if data.get("uuid") != uuid:
        raise SmokeError(f"info by uuid returned wrong entry")


def check_info_no_match(env: dict[str, str], sb: pathlib.Path) -> None:
    p = _run([str(PROJECT_SH), "info", "does-not-exist-xyz"], env)
    if p.returncode == 0:
        raise SmokeError("expected non-zero exit for no-match info")


def check_remove_requires_yes(env: dict[str, str], sb: pathlib.Path) -> None:
    p = _run([str(PROJECT_SH), "remove", "myrepo"], env)
    if p.returncode != 2:
        raise SmokeError(f"remove without --yes should exit 2, got {p.returncode}")
    if "re-run with --yes" not in p.stdout:
        raise SmokeError(f"expected confirmation hint, got: {p.stdout!r}")
    # Verify nothing was removed
    projects = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity" / "projects"
    if not list(projects.glob("*.json")):
        raise SmokeError("entry was wrongly removed without --yes")


def check_remove_with_yes(env: dict[str, str], sb: pathlib.Path) -> None:
    p = _run([str(PROJECT_SH), "remove", "myrepo", "--yes"], env)
    if p.returncode != 0:
        raise SmokeError(f"remove --yes exited {p.returncode}: {p.stderr}")
    projects = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity" / "projects"
    if list(projects.glob("*.json")):
        raise SmokeError("entry persisted after remove --yes")


def check_auto_register_via_decisions_add(env: dict[str, str], sb: pathlib.Path) -> None:
    repo = _make_fake_git_repo(sb, "myrepo2", "https://example.com/test/myrepo2.git")
    _bootstrap_trust_policy(env)
    p = _run(
        [str(DECISIONS_SH), "add",
         "--adapter", "human",
         "--decision", "smoke test",
         "--why", "verifying auto-register hook"],
        env, cwd=repo,
    )
    if p.returncode != 0:
        raise SmokeError(f"decisions add exited {p.returncode}: stderr={p.stderr}")
    if "registered new project" not in p.stderr:
        raise SmokeError(f"expected stderr notice from auto-register, got: {p.stderr!r}")
    projects = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity" / "projects"
    files = list(projects.glob("*.json"))
    if len(files) != 1:
        raise SmokeError(f"auto-register should have created 1 entry, got {len(files)}")


def check_auto_register_skips_non_git(env: dict[str, str], sb: pathlib.Path) -> None:
    """When cwd has no git repo, auto-register must be a silent no-op."""
    non_git = sb / "not-a-repo"
    non_git.mkdir(exist_ok=True)
    _bootstrap_trust_policy(env)
    p = _run(
        [str(DECISIONS_SH), "add",
         "--adapter", "human",
         "--decision", "no-git test",
         "--why", "verifying auto-register skips non-git"],
        env, cwd=non_git,
    )
    if p.returncode != 0:
        raise SmokeError(f"decisions add (non-git) exited {p.returncode}: {p.stderr}")
    if "registered new project" in p.stderr:
        raise SmokeError("auto-register should NOT fire on non-git cwd")


def check_doctor_reports_health(env: dict[str, str], sb: pathlib.Path) -> None:
    p = _run([str(DOCTOR_SH), "--json"], env)
    if p.returncode not in (0, 2):
        raise SmokeError(f"doctor exited {p.returncode}: {p.stderr}")
    # Doctor output is JSON + optional human; parse first JSON object only.
    text = p.stdout
    # Find end of JSON: count braces.
    depth = 0
    end = 0
    for i, c in enumerate(text):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    report = json.loads(text[:end])
    pr = report["checks"].get("project_registry")
    if pr is None:
        raise SmokeError("doctor report missing project_registry check")
    if "entries_count" not in pr:
        raise SmokeError(f"project_registry check missing entries_count: {pr}")
    # We expect at least 1 entry by this point in the smoke sequence
    if pr["entries_count"] < 1:
        raise SmokeError(f"expected entries_count >= 1, got {pr['entries_count']}")


def check_doctor_detects_duplicate_origin(env: dict[str, str], sb: pathlib.Path) -> None:
    """Inject a second entry with the same origin and confirm doctor flags it."""
    projects = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity" / "projects"
    existing = next(projects.glob("*.json"))
    entry = json.loads(existing.read_text())
    duplicate = dict(entry)
    duplicate["uuid"] = "00000000-0000-0000-0000-000000000001"
    duplicate["name"] = "duplicate-of-" + entry["name"]
    (projects / f"{duplicate['uuid']}.json").write_text(json.dumps(duplicate))

    p = _run([str(DOCTOR_SH), "--json"], env)
    text = p.stdout
    depth = 0
    end = 0
    for i, c in enumerate(text):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    report = json.loads(text[:end])
    pr = report["checks"]["project_registry"]
    if pr.get("status") != "warn":
        raise SmokeError(f"expected status=warn for duplicate, got {pr.get('status')}")
    if not any("duplicate_origin" in issue for issue in pr.get("issues", [])):
        raise SmokeError(f"no duplicate_origin issue surfaced: {pr.get('issues')}")
    # Cleanup
    (projects / f"{duplicate['uuid']}.json").unlink()


# ─────────────────────────────────────────────────────────────────────────
# Main

def main() -> int:
    sb = pathlib.Path(tempfile.mkdtemp(prefix="m14-project-smoke."))
    print(f"sandbox: {sb}")
    env = _sandbox_env(sb)
    pathlib.Path(env["HOME"]).mkdir(exist_ok=True)

    runner = _Runner()
    try:
        # CLI surface
        runner.check("list (empty)", lambda: check_empty_list(env, sb))
        runner.check("add from git repo", lambda: check_add_from_git_repo(env, sb))
        runner.check("add idempotent on origin", lambda: check_idempotent_add(env, sb))
        runner.check("list --json after add", lambda: check_list_shows_entry(env, sb))
        runner.check("info by name", lambda: check_info_by_name(env, sb))
        runner.check("info by uuid", lambda: check_info_by_uuid(env, sb))
        runner.check("info no-match returns nonzero", lambda: check_info_no_match(env, sb))
        runner.check("remove without --yes returns rc=2", lambda: check_remove_requires_yes(env, sb))
        runner.check("remove --yes deletes", lambda: check_remove_with_yes(env, sb))

        # Doctor + auto-register
        runner.check("auto-register fires on decisions add (git cwd)",
                     lambda: check_auto_register_via_decisions_add(env, sb))
        runner.check("auto-register no-op on non-git cwd",
                     lambda: check_auto_register_skips_non_git(env, sb))
        runner.check("doctor reports project_registry health",
                     lambda: check_doctor_reports_health(env, sb))
        runner.check("doctor flags duplicate_origin as warn",
                     lambda: check_doctor_detects_duplicate_origin(env, sb))
    finally:
        if not runner.failed:
            shutil.rmtree(sb, ignore_errors=True)

    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"project smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, msg in runner.failed:
        print(f"  FAIL  {name}  —  {msg}")
    if runner.failed:
        print(f"  sandbox preserved: {sb}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
