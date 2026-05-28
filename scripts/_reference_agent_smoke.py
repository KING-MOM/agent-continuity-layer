#!/usr/bin/env python3
"""M13.3 reference agent smoke test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


def must(cmd: list[str], *, env: dict[str, str]) -> str:
    p = run(cmd, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"command failed rc={p.returncode}: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-continuity-reference-agent.") as td:
        base = Path(td)
        env = os.environ.copy()
        env.update({
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        })
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            Path(env[key]).mkdir(parents=True, exist_ok=True)

        checks: list[tuple[str, bool]] = []

        dry = json.loads(must([
            str(REPO_ROOT / "scripts" / "reference-agent.sh"),
            "--json", "--dry-run", "--repo", "reference-agent-smoke",
        ], env=env))
        checks.append(("dry_run", dry.get("dry_run") is True and dry.get("draft", {}).get("repo") == "reference-agent-smoke"))

        appended = json.loads(must([
            str(REPO_ROOT / "scripts" / "reference-agent.sh"),
            "--json", "--repo", "reference-agent-smoke",
        ], env=env))
        decision_id = appended.get("decision_id")
        checks.append(("append", bool(decision_id) and appended.get("adapter") == "codex"))

        listed = must([
            str(REPO_ROOT / "bin" / "agent-continuity"),
            "decisions", "list", "--json", "--repo", "reference-agent-smoke", "--adapter", "codex",
        ], env=env)
        entries = [json.loads(line) for line in listed.splitlines() if line.strip()]
        checks.append(("decision_visible", any(e.get("id") == decision_id for e in entries)))
        checks.append(("author_preserved", any(e.get("author") == "reference-agent-demo" for e in entries)))
        checks.append(("refs", any("M13.3" in e.get("refs", []) for e in entries)))

        failed = [name for name, ok in checks if not ok]
        for name, ok in checks:
            print(f"{'PASS' if ok else 'FAIL'} {name}")
        if failed:
            print(f"FAILED: {', '.join(failed)}")
            return 1
        print(f"reference-agent smoke passed ({len(checks)}/{len(checks)})")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
