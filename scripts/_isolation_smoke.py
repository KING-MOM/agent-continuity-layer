#!/usr/bin/env python3
"""_isolation_smoke.py — v0.5.3 cross-team isolation smoke.

The threat model: an operator works on personal projects AND on a Life
Agent-style team. Personal data MUST NOT end up in the team's memory
repo, even if the operator misroutes a decision or the substrate has a
sync bug. v0.5.3 closes leak vectors v0.5.1 left open.

Suite:

  L1   syncing personal data to a personal repo: contexts present
  L2   syncing personal data to a TEAM repo: contexts NOT present
  L3   syncing personal-team_id project to a team repo: project absent
  L4   syncing team-team_id project to a team repo: project present
  L5   syncing device-identity to personal repo: present
  L6   syncing device-identity to team repo where local is NOT admin: absent
  L7   syncing device-identity to team repo where local IS admin: present
  L8   team audit detects a manually-injected cross-team decision
  L9   team audit detects a context file in a team repo
  L10  team audit succeeds on a clean team repo
  L11  pre-commit hook installs and is executable
  L12  pre-commit hook calls team audit and reports findings on bad commit
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


def _env(cfg: pathlib.Path, state: pathlib.Path, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(cfg)
    env["XDG_STATE_HOME"] = str(state)
    env.pop("AGENT_CONTINUITY_TEAM_ID", None)
    env.pop("AGENT_CONTINUITY_TEAM_REPO", None)
    env.update(extra)
    return env


def _run(args: list[str], env: dict[str, str], check_rc: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
    if check_rc and r.returncode != 0:
        raise SmokeError(f"{args[1] if len(args)>1 else args[0]} rc={r.returncode}: {r.stderr}")
    return r


def _setup_team_repo(cfg: pathlib.Path, team_repo: pathlib.Path, env_base: dict[str, str], team_name: str = "Test Team") -> str:
    """Initialize a team memory repo. Returns the team_id."""
    env = {**env_base, "XDG_CONFIG_HOME": str(cfg)}
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], env)
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(team_repo), "init"], env)
    _run([sys.executable, str(SCRIPTS / "_team.py"), "--path", str(team_repo), "init", "--team-name", team_name], env)
    return json.loads((team_repo / "team-manifest.json").read_text())["team_id"]


def _make_personal_repo(env: dict[str, str], path: pathlib.Path) -> None:
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(path), "init"], env)


def _add_decision(env: dict[str, str], team_id: str | None, decision: str) -> None:
    args = [sys.executable, str(SCRIPTS / "_decisions.py"), "add",
            "--adapter", "human", "--decision", decision, "--why", "test",
            "--ref", "smoke", "--repo", "iso-test"]
    if team_id:
        args.extend(["--team-id", team_id])
    _run(args, env)


# ──────────────────────────────────────────────────────────────────

def t_sync_personal_contexts_present(td: pathlib.Path) -> None:
    cfg = td / "L1-cfg"; state = td / "L1-state"; repo = td / "L1-personal"
    env = _env(cfg, state)
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], env)
    _make_personal_repo(env, repo)
    # Create a substrate-local context-snapshot for the test (mimics
    # core/context-snapshot.json existing)
    snapshot_src = REPO_ROOT / "core" / "context-snapshot.json"
    if not snapshot_src.exists():
        raise SmokeError(f"prerequisite: {snapshot_src} must exist for this test")
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(repo), "export"], env)
    ctx = repo / "contexts" / "agent-continuity-layer.context.json"
    if not ctx.exists():
        raise SmokeError(f"personal sync did not export context to {ctx}")


def t_sync_team_no_contexts(td: pathlib.Path) -> None:
    cfg = td / "L2-cfg"; state = td / "L2-state"; team_repo = td / "L2-team"
    env_base = _env(cfg, state)
    _setup_team_repo(cfg, team_repo, env_base)
    env = env_base
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(team_repo), "export"], env)
    contexts_dir = team_repo / "contexts"
    # .gitkeep is planted by `git-memory init` to keep the dir tracked;
    # it's git plumbing, not substrate content. Any OTHER file is a leak.
    leaked = [p for p in contexts_dir.glob("*") if p.name != ".gitkeep"] if contexts_dir.exists() else []
    if leaked:
        raise SmokeError(f"contexts leaked to team repo: {[str(p.name) for p in leaked]}")


def _make_project_entry(env: dict[str, str], uuid: str, team_id: str | None) -> None:
    """Write a project entry to $XDG_CONFIG_HOME/agent-continuity/projects/."""
    projects = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity" / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {"schema_version": "1.0", "uuid": uuid, "name": f"proj-{uuid[:8]}"}
    if team_id:
        entry["team_id"] = team_id
    (projects / f"{uuid}.json").write_text(json.dumps(entry, indent=2) + "\n")


def t_personal_project_not_in_team_repo(td: pathlib.Path) -> None:
    cfg = td / "L3-cfg"; state = td / "L3-state"; team_repo = td / "L3-team"
    env_base = _env(cfg, state)
    _setup_team_repo(cfg, team_repo, env_base)
    _make_project_entry(env_base, "11111111-aaaa-bbbb-cccc-personal-proj", None)
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(team_repo), "export"], env_base)
    leaked = list((team_repo / "projects").glob("11111111-*.json"))
    if leaked:
        raise SmokeError(f"personal project leaked into team repo: {leaked}")


def t_team_project_does_export(td: pathlib.Path) -> None:
    cfg = td / "L4-cfg"; state = td / "L4-state"; team_repo = td / "L4-team"
    env_base = _env(cfg, state)
    team_id = _setup_team_repo(cfg, team_repo, env_base)
    _make_project_entry(env_base, "22222222-aaaa-bbbb-cccc-team-proj", team_id)
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(team_repo), "export"], env_base)
    exported = list((team_repo / "projects").glob("22222222-*.json"))
    if not exported:
        raise SmokeError("team-scoped project did NOT export to team repo")


def _write_device_identity(env: dict[str, str], human_actor_id: str) -> None:
    """Write a device-identity.json under XDG_CONFIG_HOME."""
    cfg = pathlib.Path(env["XDG_CONFIG_HOME"]) / "agent-continuity"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "device-identity.json").write_text(json.dumps({
        "device_id": "test-device",
        "hostname": "test-mac",
        "human_actor_id": human_actor_id,
    }, indent=2) + "\n")


def t_device_identity_personal(td: pathlib.Path) -> None:
    cfg = td / "L5-cfg"; state = td / "L5-state"; repo = td / "L5-personal"
    env = _env(cfg, state)
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], env)
    _write_device_identity(env, "human:test:personal")
    _make_personal_repo(env, repo)
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(repo), "export"], env)
    devs = list((repo / "devices").glob("*.json"))
    if not devs:
        raise SmokeError("device-identity not exported to personal repo")


def t_device_identity_team_not_admin(td: pathlib.Path) -> None:
    cfg = td / "L6-cfg"; state = td / "L6-state"; team_repo = td / "L6-team"
    env_base = _env(cfg, state)
    _setup_team_repo(cfg, team_repo, env_base)
    # Replace the local key to one that is NOT in admin_set
    # First save the admin manifest, then overwrite the device key
    cfg_dir = cfg / "agent-continuity"
    new_key_env = _env(td / "L6-other-cfg", td / "L6-other-state")
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate", "--human-actor-id", "human:test:non-admin"], new_key_env)
    # Copy the non-admin key into the original cfg path so device-identity test routes through it
    shutil.copy(td / "L6-other-cfg" / "agent-continuity" / "device-key.json",
                cfg_dir / "device-key.json")
    _write_device_identity(env_base, "human:test:non-admin")
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(team_repo), "export"], env_base)
    leaked = list((team_repo / "devices").glob("*.json"))
    if leaked:
        raise SmokeError(f"non-admin device identity leaked to team repo: {leaked}")


def t_device_identity_team_admin(td: pathlib.Path) -> None:
    cfg = td / "L7-cfg"; state = td / "L7-state"; team_repo = td / "L7-team"
    env_base = _env(cfg, state)
    _setup_team_repo(cfg, team_repo, env_base)
    # The admin's local key IS the founding admin; write a matching device-identity
    key = json.loads((cfg / "agent-continuity" / "device-key.json").read_text())
    _write_device_identity(env_base, key["human_actor_id"])
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(team_repo), "export"], env_base)
    exported = list((team_repo / "devices").glob("*.json"))
    if not exported:
        raise SmokeError("admin's device-identity did NOT export to team repo")


def t_audit_detects_cross_team(td: pathlib.Path) -> None:
    cfg = td / "L8-cfg"; state = td / "L8-state"; team_repo = td / "L8-team"
    env_base = _env(cfg, state)
    team_id = _setup_team_repo(cfg, team_repo, env_base)
    # Manually inject a personal entry into the team repo's decisions.jsonl
    decisions_path = team_repo / "decisions" / "decisions.jsonl"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    leaked_entry = {
        "schema_version": "2.0", "id": "leak1", "ts": "2026-01-01T00:00:00Z",
        "team_id": "personal",
        "adapter": "human", "repo": "lugares-virtuales",
        "decision": "Personal LV fiscal decision", "why": "should not be here",
        "refs": ["lv:internal"],
    }
    decisions_path.write_text(json.dumps(leaked_entry) + "\n")
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(team_repo), "audit"], env_base, check_rc=False)
    if r.returncode != 1:
        raise SmokeError(f"audit should have detected mismatch but rc={r.returncode}: {r.stdout}")
    if "team-mismatch" not in r.stdout:
        raise SmokeError(f"audit output missing team-mismatch finding: {r.stdout!r}")


def t_audit_detects_context_in_team_repo(td: pathlib.Path) -> None:
    cfg = td / "L9-cfg"; state = td / "L9-state"; team_repo = td / "L9-team"
    env_base = _env(cfg, state)
    _setup_team_repo(cfg, team_repo, env_base)
    # Manually plant a context file
    ctx_dir = team_repo / "contexts"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    (ctx_dir / "agent-continuity-layer.context.json").write_text('{"leak": true}')
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(team_repo), "audit"], env_base, check_rc=False)
    if r.returncode != 1:
        raise SmokeError(f"audit should have detected context leak but rc={r.returncode}: {r.stdout}")
    if "context-in-team-repo" not in r.stdout:
        raise SmokeError(f"audit output missing context-in-team-repo finding: {r.stdout!r}")


def t_audit_clean_team_repo(td: pathlib.Path) -> None:
    cfg = td / "L10-cfg"; state = td / "L10-state"; team_repo = td / "L10-team"
    env_base = _env(cfg, state)
    team_id = _setup_team_repo(cfg, team_repo, env_base)
    _add_decision(env_base, team_id, "Legit team decision")
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"), "--path", str(team_repo), "export"], env_base)
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(team_repo), "audit"], env_base, check_rc=False)
    if r.returncode != 0:
        raise SmokeError(f"audit should succeed on clean repo but rc={r.returncode}: {r.stdout}")
    if "no leaks detected" not in r.stdout:
        raise SmokeError(f"clean audit output unexpected: {r.stdout!r}")


def t_install_hook(td: pathlib.Path) -> None:
    cfg = td / "L11-cfg"; state = td / "L11-state"; team_repo = td / "L11-team"
    env_base = _env(cfg, state)
    _setup_team_repo(cfg, team_repo, env_base)
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(team_repo), "install-hook"], env_base)
    hook = team_repo / ".git" / "hooks" / "pre-commit"
    if not hook.exists():
        raise SmokeError(f"hook not installed at {hook}")
    mode = oct(hook.stat().st_mode & 0o777)
    if "75" not in mode and "55" not in mode:
        raise SmokeError(f"hook mode {mode}, expected executable")
    # Re-install should be refused without --force
    r = _run([sys.executable, str(SCRIPTS / "_team.py"),
              "--path", str(team_repo), "install-hook"], env_base, check_rc=False)
    if r.returncode == 0:
        raise SmokeError("re-install without --force should have failed")


def t_hook_calls_audit_logic(td: pathlib.Path) -> None:
    """The hook script calls `agent-continuity team audit`. We don't have
    a system-installed agent-continuity here, so verify that the hook
    script itself is well-formed and calls the right command."""
    cfg = td / "L12-cfg"; state = td / "L12-state"; team_repo = td / "L12-team"
    env_base = _env(cfg, state)
    _setup_team_repo(cfg, team_repo, env_base)
    _run([sys.executable, str(SCRIPTS / "_team.py"),
          "--path", str(team_repo), "install-hook"], env_base)
    hook_body = (team_repo / ".git" / "hooks" / "pre-commit").read_text()
    if "agent-continuity team --path" not in hook_body or "audit" not in hook_body:
        raise SmokeError(f"hook body does not invoke team audit: {hook_body[:200]}")
    if "--no-verify" not in hook_body:
        raise SmokeError("hook should document the --no-verify bypass for emergencies")


# ──────────────────────────────────────────────────────────────────

def main() -> int:
    td = pathlib.Path(tempfile.mkdtemp(prefix="isolation-smoke."))
    print(f"sandbox: {td}\n")
    runner = _Runner()
    try:
        runner.check("L1: contexts copy to personal repos", lambda: t_sync_personal_contexts_present(td))
        runner.check("L2: contexts do NOT copy to team repos", lambda: t_sync_team_no_contexts(td))
        runner.check("L3: personal project absent from team repo sync", lambda: t_personal_project_not_in_team_repo(td))
        runner.check("L4: team project present in team repo sync", lambda: t_team_project_does_export(td))
        runner.check("L5: device-identity copies to personal repos", lambda: t_device_identity_personal(td))
        runner.check("L6: device-identity withheld from team repos when local is NOT admin", lambda: t_device_identity_team_not_admin(td))
        runner.check("L7: device-identity copies to team repos when local IS admin", lambda: t_device_identity_team_admin(td))
        runner.check("L8: audit detects manually-injected cross-team decision", lambda: t_audit_detects_cross_team(td))
        runner.check("L9: audit detects context file in team repo", lambda: t_audit_detects_context_in_team_repo(td))
        runner.check("L10: audit succeeds on clean team repo", lambda: t_audit_clean_team_repo(td))
        runner.check("L11: install-hook installs executable pre-commit hook + refuses overwrite", lambda: t_install_hook(td))
        runner.check("L12: hook body invokes `team audit` and documents --no-verify", lambda: t_hook_calls_audit_logic(td))
    finally:
        if not runner.failed:
            shutil.rmtree(td, ignore_errors=True)
    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"isolation smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, err in runner.failed:
        print(f"  FAIL  {name}: {err}")
    return 0 if not runner.failed else 1


if __name__ == "__main__":
    sys.exit(main())
