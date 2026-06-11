#!/usr/bin/env python3
"""_routing_smoke.py — v0.5.1 team-routing smoke.

The load-bearing test: a single operator with both a personal memory
repo and a team memory repo on the same machine writes a mix of personal
and team-scoped decisions. After git-memory sync to both repos, each
repo must contain ONLY its own decisions — no cross-pollination.

Suite:

  R1   default team_id is 'personal' (no flag, no env)
  R2   --team-id explicit value lands on entry
  R3   --team-repo resolves team_id from team-manifest.json
  R4   AGENT_CONTINUITY_TEAM_ID env var sets team_id
  R5   AGENT_CONTINUITY_TEAM_REPO env var resolves team_id from manifest
  R6   --team-id beats AGENT_CONTINUITY_TEAM_ID (explicit > env)
  R7   sync to personal repo (no manifest) exports ONLY personal entries
  R8   sync to team repo (with manifest) exports ONLY that team's entries
  R9   legacy entries without team_id route to personal
  R10  --team-repo to a path with no manifest warns + routes to personal
  R11  no-match sync writes no decisions.jsonl (empty filter result)
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
    # Strip routing env so each test starts clean
    env.pop("AGENT_CONTINUITY_TEAM_ID", None)
    env.pop("AGENT_CONTINUITY_TEAM_REPO", None)
    env.update(extra)
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)


def _add(env: dict[str, str], **kwargs: str) -> tuple[str, str]:
    """Run decisions add and return (entry_id, stderr)."""
    args = [
        sys.executable, str(SCRIPTS / "_decisions.py"), "add",
        "--adapter", kwargs.pop("adapter", "human"),
        "--decision", kwargs.pop("decision"),
        "--why", kwargs.pop("why", "test"),
        "--ref", kwargs.pop("ref", "smoke"),
        "--repo", kwargs.pop("repo", "test-repo"),
    ]
    if "team_id" in kwargs:
        args.extend(["--team-id", kwargs.pop("team_id")])
    if "team_repo" in kwargs:
        args.extend(["--team-repo", kwargs.pop("team_repo")])
    r = _run(args, env)
    if r.returncode != 0:
        raise SmokeError(f"add rc={r.returncode}: {r.stderr}")
    return r.stdout.strip(), r.stderr


def _init_team_manifest(cfg: pathlib.Path, team_repo: pathlib.Path, team_name: str = "Test Team") -> str:
    """Init a team-aware memory repo: git-memory init + team manifest. Returns team_id."""
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(cfg)
    _run([sys.executable, str(SCRIPTS / "_key.py"), "generate"], env)
    _run([sys.executable, str(SCRIPTS / "_git_memory.py"),
          "--path", str(team_repo), "init"], env)
    _run([sys.executable, str(SCRIPTS / "_team.py"),
          "--path", str(team_repo), "init", "--team-name", team_name], env)
    manifest = json.loads((team_repo / "team-manifest.json").read_text())
    return manifest["team_id"]


def _read_entries(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ──────────────────────────────────────────────────────────────────

def t_default_personal(td: pathlib.Path) -> None:
    cfg = td / "R1-cfg"; state = td / "R1-state"
    env = _env(cfg, state)
    _add(env, decision="No flags")
    entries = _read_entries(state / "agent-continuity" / "decisions.jsonl")
    assert entries[0]["team_id"] == "personal", entries[0]


def t_explicit_team_id(td: pathlib.Path) -> None:
    cfg = td / "R2-cfg"; state = td / "R2-state"
    env = _env(cfg, state)
    _add(env, decision="Explicit team_id", team_id="my-team-uuid-xxx")
    entries = _read_entries(state / "agent-continuity" / "decisions.jsonl")
    assert entries[0]["team_id"] == "my-team-uuid-xxx", entries[0]


def t_team_repo_flag(td: pathlib.Path) -> None:
    cfg = td / "R3-cfg"; state = td / "R3-state"
    team_repo = td / "R3-team"
    tid = _init_team_manifest(cfg, team_repo)
    env = _env(cfg, state)
    _add(env, decision="Via --team-repo", team_repo=str(team_repo))
    entries = _read_entries(state / "agent-continuity" / "decisions.jsonl")
    assert entries[0]["team_id"] == tid, f"got {entries[0]['team_id']}, expected {tid}"


def t_env_team_id(td: pathlib.Path) -> None:
    cfg = td / "R4-cfg"; state = td / "R4-state"
    env = _env(cfg, state, AGENT_CONTINUITY_TEAM_ID="env-team-zzz")
    _add(env, decision="Via env team_id")
    entries = _read_entries(state / "agent-continuity" / "decisions.jsonl")
    assert entries[0]["team_id"] == "env-team-zzz", entries[0]


def t_env_team_repo(td: pathlib.Path) -> None:
    cfg = td / "R5-cfg"; state = td / "R5-state"
    team_repo = td / "R5-team"
    tid = _init_team_manifest(cfg, team_repo)
    env = _env(cfg, state, AGENT_CONTINUITY_TEAM_REPO=str(team_repo))
    _add(env, decision="Via env team_repo")
    entries = _read_entries(state / "agent-continuity" / "decisions.jsonl")
    assert entries[0]["team_id"] == tid, f"got {entries[0]['team_id']}, expected {tid}"


def t_explicit_beats_env(td: pathlib.Path) -> None:
    cfg = td / "R6-cfg"; state = td / "R6-state"
    env = _env(cfg, state, AGENT_CONTINUITY_TEAM_ID="env-team")
    _add(env, decision="Flag wins", team_id="flag-team")
    entries = _read_entries(state / "agent-continuity" / "decisions.jsonl")
    assert entries[0]["team_id"] == "flag-team", entries[0]


def t_sync_personal_filters_team(td: pathlib.Path) -> None:
    """Sync to a memory repo with NO team-manifest exports only personal entries."""
    cfg = td / "R7-cfg"; state = td / "R7-state"
    personal_repo = td / "R7-personal"
    env = _env(cfg, state)
    # Mix: 2 personal, 2 team
    _add(env, decision="Personal A", ref="personal:1")
    _add(env, decision="Team A", team_id="team-xxx", ref="team:1")
    _add(env, decision="Personal B", ref="personal:2")
    _add(env, decision="Team B", team_id="team-xxx", ref="team:2")
    # Sync to personal repo
    r = _run([sys.executable, str(SCRIPTS / "_git_memory.py"),
              "--path", str(personal_repo), "init"], env)
    assert r.returncode == 0, r.stderr
    r = _run([sys.executable, str(SCRIPTS / "_git_memory.py"),
              "--path", str(personal_repo), "export"], env)
    assert r.returncode == 0, r.stderr
    exported = _read_entries(personal_repo / "decisions" / "decisions.jsonl")
    teams = sorted({e["team_id"] for e in exported})
    assert teams == ["personal"], f"personal sync leaked team data: {teams}"
    assert len(exported) == 2, f"expected 2 personal entries, got {len(exported)}"


def t_sync_team_filters_personal(td: pathlib.Path) -> None:
    """Sync to a memory repo WITH a team-manifest exports only that team's entries."""
    cfg = td / "R8-cfg"; state = td / "R8-state"
    team_repo = td / "R8-team"
    tid = _init_team_manifest(cfg, team_repo)
    env = _env(cfg, state)
    # Mix: 2 personal, 2 team
    _add(env, decision="Personal A")
    _add(env, decision="Team A", team_id=tid, ref="team:1")
    _add(env, decision="Personal B")
    _add(env, decision="Team B", team_id=tid, ref="team:2")
    # Sync to team repo (team-manifest already there)
    r = _run([sys.executable, str(SCRIPTS / "_git_memory.py"),
              "--path", str(team_repo), "export"], env)
    assert r.returncode == 0, r.stderr
    exported = _read_entries(team_repo / "decisions" / "decisions.jsonl")
    teams = sorted({e["team_id"] for e in exported})
    assert teams == [tid], f"team sync leaked: {teams}"
    assert len(exported) == 2, f"expected 2 team entries, got {len(exported)}"


def t_legacy_entries_route_personal(td: pathlib.Path) -> None:
    """Pre-v0.5.1 entries without team_id are treated as personal on sync."""
    cfg = td / "R9-cfg"; state = td / "R9-state"
    personal_repo = td / "R9-personal"
    decisions = state / "agent-continuity" / "decisions.jsonl"
    decisions.parent.mkdir(parents=True, exist_ok=True)
    # Hand-craft a legacy entry with no team_id field
    legacy = {
        "schema_version": "1.0",
        "id": "legacy123", "ts": "2026-01-01T00:00:00Z",
        "adapter": "human", "repo": "legacy",
        "decision": "Legacy entry", "why": "no team_id",
        "refs": ["legacy:1"],
    }
    decisions.write_text(json.dumps(legacy) + "\n")
    env = _env(cfg, state)
    r = _run([sys.executable, str(SCRIPTS / "_git_memory.py"),
              "--path", str(personal_repo), "init"], env)
    assert r.returncode == 0, r.stderr
    r = _run([sys.executable, str(SCRIPTS / "_git_memory.py"),
              "--path", str(personal_repo), "export"], env)
    assert r.returncode == 0, r.stderr
    exported = _read_entries(personal_repo / "decisions" / "decisions.jsonl")
    assert len(exported) == 1, f"legacy entry should route to personal: got {len(exported)}"


def t_team_repo_path_no_manifest_warns(td: pathlib.Path) -> None:
    cfg = td / "R10-cfg"; state = td / "R10-state"
    bogus = td / "R10-bogus"; bogus.mkdir(parents=True, exist_ok=True)
    env = _env(cfg, state)
    eid, stderr = _add(env, decision="Bogus team-repo", team_repo=str(bogus))
    if "no team-manifest.json" not in stderr:
        raise SmokeError(f"expected warning about missing manifest; got: {stderr!r}")
    entries = _read_entries(state / "agent-continuity" / "decisions.jsonl")
    assert entries[0]["team_id"] == "personal", entries[0]


def t_no_match_no_file(td: pathlib.Path) -> None:
    """Sync where NO entries match the target team writes no decisions.jsonl."""
    cfg = td / "R11-cfg"; state = td / "R11-state"
    team_repo = td / "R11-team"
    tid = _init_team_manifest(cfg, team_repo)
    env = _env(cfg, state)
    # Only personal entries; nothing for the team
    _add(env, decision="Personal only")
    r = _run([sys.executable, str(SCRIPTS / "_git_memory.py"),
              "--path", str(team_repo), "export"], env)
    assert r.returncode == 0, r.stderr
    dst = team_repo / "decisions" / "decisions.jsonl"
    if dst.exists():
        raise SmokeError(f"empty filter wrote {dst} (should not create file)")


# ──────────────────────────────────────────────────────────────────

def main() -> int:
    td = pathlib.Path(tempfile.mkdtemp(prefix="routing-smoke."))
    print(f"sandbox: {td}\n")
    runner = _Runner()
    try:
        runner.check("R1: default team_id is 'personal'", lambda: t_default_personal(td))
        runner.check("R2: --team-id explicit value", lambda: t_explicit_team_id(td))
        runner.check("R3: --team-repo resolves from manifest", lambda: t_team_repo_flag(td))
        runner.check("R4: AGENT_CONTINUITY_TEAM_ID env var", lambda: t_env_team_id(td))
        runner.check("R5: AGENT_CONTINUITY_TEAM_REPO env var", lambda: t_env_team_repo(td))
        runner.check("R6: --team-id beats env (explicit > env)", lambda: t_explicit_beats_env(td))
        runner.check("R7: sync personal repo exports ONLY personal entries", lambda: t_sync_personal_filters_team(td))
        runner.check("R8: sync team repo exports ONLY that team's entries", lambda: t_sync_team_filters_personal(td))
        runner.check("R9: legacy entries (no team_id) route to personal", lambda: t_legacy_entries_route_personal(td))
        runner.check("R10: --team-repo with no manifest warns + routes personal", lambda: t_team_repo_path_no_manifest_warns(td))
        runner.check("R11: no-match sync writes no decisions.jsonl", lambda: t_no_match_no_file(td))
    finally:
        if not runner.failed:
            shutil.rmtree(td, ignore_errors=True)
    total = len(runner.passed) + len(runner.failed)
    print()
    print(f"routing smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, err in runner.failed:
        print(f"  FAIL  {name}: {err}")
    return 0 if not runner.failed else 1


if __name__ == "__main__":
    sys.exit(main())
