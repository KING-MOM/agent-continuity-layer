#!/usr/bin/env python3
"""M13.4 unified connect smoke test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)


def must_json(cmd: list[str], *, env: dict[str, str], expected_rc: tuple[int, ...]) -> dict:
    p = run(cmd, env=env)
    if p.returncode not in expected_rc:
        raise RuntimeError(f"unexpected rc={p.returncode}: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return json.loads(p.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-continuity-connect.") as td:
        base = Path(td)
        env = os.environ.copy()
        env.update({
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        })
        home = Path(env["HOME"])
        for p in (
            home / ".claude",
            home / ".codex",
            home / ".openclaw" / "workspace",
            Path(env["XDG_CONFIG_HOME"]),
            Path(env["XDG_STATE_HOME"]),
            Path(env["XDG_CACHE_HOME"]),
        ):
            p.mkdir(parents=True, exist_ok=True)

        checks: list[tuple[str, bool]] = []

        dry = must_json([str(REPO_ROOT / "bin" / "agent-continuity"), "connect", "doctor", "--json"], env=env, expected_rc=(2,))
        checks.append(("dry_run_pending", dry.get("summary", {}).get("pending_writes") == 6))
        checks.append(("dry_run_no_errors", dry.get("summary", {}).get("errors") == 0))
        checks.append(("dry_run_seven_entries", dry.get("summary", {}).get("entries") == 7))

        for target in ("claude-desktop", "cursor", "zed"):
            single = must_json([str(REPO_ROOT / "bin" / "agent-continuity"), "connect", target, "--json"], env=env, expected_rc=(2,))
            checks.append((f"{target}_single_pending", single.get("summary", {}).get("entries") == 1 and single.get("summary", {}).get("pending_writes") == 1))

        for target in ("claude", "codex", "openclaw-skill"):
            single = must_json([str(REPO_ROOT / "bin" / "agent-continuity"), "connect", target, "--json"], env=env, expected_rc=(2,))
            checks.append((f"{target}_single_skill", single.get("summary", {}).get("entries") == 1))

        applied = must_json([str(REPO_ROOT / "bin" / "agent-continuity"), "connect", "all", "--apply", "--json"], env=env, expected_rc=(0,))
        checks.append(("apply_no_pending", applied.get("summary", {}).get("pending_writes") == 0))
        checks.append(("apply_no_errors", applied.get("summary", {}).get("errors") == 0))

        claude_cfg = home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        cursor_cfg = home / ".cursor" / "mcp.json"
        zed_cfg = home / ".zed" / "settings.json"
        for name, path, section in (
            ("claude_config", claude_cfg, "mcpServers"),
            ("cursor_config", cursor_cfg, "mcpServers"),
            ("zed_config", zed_cfg, "context_servers"),
        ):
            data = json.loads(path.read_text(encoding="utf-8"))
            entry = data.get(section, {}).get("agent-continuity")
            checks.append((name, isinstance(entry, dict) and entry.get("command") == "agent-continuity" and entry.get("args") == ["mcp", "serve"]))

        for name, path in (
            ("claude_skill", home / ".claude" / "skills" / "agent-continuity" / "SKILL.md"),
            ("codex_skill", home / ".codex" / "skills" / "agent-continuity" / "SKILL.md"),
            ("openclaw_skill", home / ".openclaw" / "workspace" / "skills" / "agent-continuity" / "SKILL.md"),
        ):
            checks.append((name, path.exists() and "Agent Continuity Layer" in path.read_text(encoding="utf-8")))

        rerun = must_json([str(REPO_ROOT / "bin" / "agent-continuity"), "connect", "doctor", "--json"], env=env, expected_rc=(0,))
        checks.append(("rerun_clean", rerun.get("summary", {}).get("pending_writes") == 0 and rerun.get("summary", {}).get("errors") == 0))

        for target in ("claude-desktop", "cursor", "zed", "claude", "codex", "openclaw-skill", "openclaw"):
            single = must_json([str(REPO_ROOT / "bin" / "agent-continuity"), "connect", target, "--json"], env=env, expected_rc=(0,))
            checks.append((f"{target}_single_after_apply", single.get("summary", {}).get("entries") == 1 and single.get("summary", {}).get("errors") == 0))

        failed = [name for name, ok in checks if not ok]
        for name, ok in checks:
            print(f"{'PASS' if ok else 'FAIL'} {name}")
        if failed:
            print(f"FAILED: {', '.join(failed)}")
            return 1
        print(f"connect smoke passed ({len(checks)}/{len(checks)})")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
