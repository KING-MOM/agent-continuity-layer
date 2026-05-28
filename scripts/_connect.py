#!/usr/bin/env python3
"""Unified adapter connector (M13.4).

Connects local adapter hosts to the same substrate entry point:
`agent-continuity mcp serve` for GUI/MCP clients, and thin skills for local
agent homes. Dry-run by default, --apply writes with backups.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
INSTALL_VERSION = "1.0"
MCP_SERVER_NAME = "agent-continuity"
MCP_COMMAND = "agent-continuity"
MCP_ARGS = ["mcp", "serve"]

MCP_TARGETS: dict[str, dict[str, Any]] = {
    "claude-desktop": {
        "label": "Claude Desktop",
        "path": HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        "section": "mcpServers",
        "entry": {"command": MCP_COMMAND, "args": MCP_ARGS},
    },
    "cursor": {
        "label": "Cursor",
        "path": HOME / ".cursor" / "mcp.json",
        "section": "mcpServers",
        "entry": {"type": "stdio", "command": MCP_COMMAND, "args": MCP_ARGS},
    },
    "zed": {
        "label": "Zed",
        "path": (HOME / ".zed" / "settings.json") if sys.platform == "darwin" else (XDG_CONFIG_HOME / "zed" / "settings.json"),
        "section": "context_servers",
        "entry": {"command": MCP_COMMAND, "args": MCP_ARGS},
    },
}

SKILL_AGENTS = ("claude", "codex", "openclaw")
TARGET_CHOICES = [
    "all",
    "mcp",
    "skills",
    "claude-desktop",
    "cursor",
    "zed",
    "claude",
    "codex",
    "openclaw-skill",
    "openclaw",
    "doctor",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_filesafe() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup(path: Path) -> Path:
    backup = path.with_name(f"{path.name}.bak-{_now_filesafe()}")
    shutil.copy2(path, backup)
    return backup


def _read_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {}, f"invalid JSON: {e}"
    if not isinstance(data, dict):
        return {}, f"expected top-level JSON object, got {type(data).__name__}"
    return data, None


def _plan_mcp(name: str) -> dict[str, Any]:
    spec = MCP_TARGETS[name]
    path = Path(spec["path"])
    section = str(spec["section"])
    desired_entry = dict(spec["entry"])
    data, error = _read_json(path)
    out: dict[str, Any] = {
        "kind": "mcp-config",
        "name": name,
        "label": spec["label"],
        "path": str(path),
        "section": section,
        "desired_entry": desired_entry,
        "state": None,
        "action": None,
        "error": error,
        "backup_path": None,
        "applied": False,
    }
    if error:
        out["state"] = "error"
        out["action"] = "skip-error"
        return out
    section_obj = data.get(section)
    if section_obj is None:
        section_obj = {}
    if not isinstance(section_obj, dict):
        out["state"] = "error"
        out["action"] = "skip-error"
        out["error"] = f"{section} exists but is {type(section_obj).__name__}, expected object"
        return out
    current = section_obj.get(MCP_SERVER_NAME)
    out["current_entry"] = current
    if current == desired_entry:
        out["state"] = "connected"
        out["action"] = "skip-exact"
    elif current is None:
        out["state"] = "missing"
        out["action"] = "write"
    else:
        out["state"] = "drifted"
        out["action"] = "overwrite"
    return out


def _apply_mcp(plan: dict[str, Any]) -> dict[str, Any]:
    if plan["action"] in ("skip-exact", "skip-error"):
        if plan["action"] == "skip-exact":
            plan["applied"] = True
        return plan
    path = Path(plan["path"])
    data, error = _read_json(path)
    if error:
        plan["error"] = error
        return plan
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            plan["backup_path"] = str(_backup(path))
        section = plan["section"]
        if not isinstance(data.get(section), dict):
            data[section] = {}
        data[section][MCP_SERVER_NAME] = plan["desired_entry"]
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        plan["applied"] = True
    except Exception as e:
        plan["error"] = f"write failed: {e}"
    return plan


def _run_skill_installer(*args: str) -> tuple[int, str, str]:
    cmd = [str(REPO_ROOT / "scripts" / "install-thin-skills.sh"), *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr


def _skill_report(apply: bool, agent: str | None = None) -> dict[str, Any]:
    args = ["--json"]
    if apply:
        args.insert(0, "--apply")
    if agent:
        args.extend(["--agent", agent])
    rc, out, err = _run_skill_installer(*args)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = None
    return {
        "kind": "thin-skills",
        "name": f"{agent}-skill" if agent else "thin-skills",
        "agents": [agent] if agent else list(SKILL_AGENTS),
        "agent": agent,
        "state": "ok" if rc == 0 else ("pending" if rc == 2 else "error"),
        "action": "delegate-install-thin-skills",
        "applied": apply and rc in (0, 2),
        "returncode": rc,
        "report": payload,
        "stdout": None if payload is not None else out,
        "stderr": err,
    }


def _openclaw_bridge_status() -> dict[str, Any]:
    mjs = HOME / ".openclaw" / "workspace" / "scripts" / "agent-worker.mjs"
    extension = HOME / ".openclaw" / "workspace" / ".openclaw" / "extensions" / "agent-worker" / "index.js"
    return {
        "kind": "bridge-status",
        "name": "openclaw-bridge",
        "state": "connected" if mjs.exists() and extension.exists() else "info",
        "action": "inspect-only",
        "agent_worker_mjs": str(mjs),
        "agent_worker_mjs_present": mjs.exists(),
        "extension_index": str(extension),
        "extension_index_present": extension.exists(),
        "note": "OpenClaw bridge is host-side; connect reports it but does not install or rewrite it.",
    }


def build_report(*, apply: bool, include: list[str]) -> dict[str, Any]:
    targets = include or ["all"]
    include_mcp = "all" in targets or "mcp" in targets
    include_skills = "all" in targets or "skills" in targets
    skill_targets = [t for t in targets if t in ("claude", "codex", "openclaw-skill")]
    mcp_targets = [t for t in targets if t in MCP_TARGETS]
    include_openclaw = "all" in targets or "openclaw" in targets

    entries: list[dict[str, Any]] = []
    if include_mcp:
        for name in MCP_TARGETS:
            p = _plan_mcp(name)
            if apply:
                p = _apply_mcp(p)
            entries.append(p)
    for name in mcp_targets:
        p = _plan_mcp(name)
        if apply:
            p = _apply_mcp(p)
        entries.append(p)
    if include_skills:
        for agent in SKILL_AGENTS:
            entries.append(_skill_report(apply, agent))
    for target in skill_targets:
        agent = "openclaw" if target == "openclaw-skill" else target
        entries.append(_skill_report(apply, agent))
    if include_openclaw:
        entries.append(_openclaw_bridge_status())

    errors = sum(1 for e in entries if e.get("state") == "error" or e.get("error"))
    pending = sum(
        1 for e in entries
        if (
            (e.get("action") in ("write", "overwrite") and not e.get("applied"))
            or (e.get("state") == "pending" and not e.get("applied"))
        )
    )
    connected = sum(1 for e in entries if e.get("state") in ("connected", "ok"))
    return {
        "version": INSTALL_VERSION,
        "ran_at": _now(),
        "device": socket.gethostname(),
        "mode": "apply" if apply else "dry-run",
        "entries": entries,
        "summary": {"entries": len(entries), "connected_or_ok": connected, "pending_writes": pending, "errors": errors},
    }


def render_human(report: dict[str, Any]) -> str:
    L: list[str] = []
    L.append(f"agent-continuity connect v{report['version']} - {report['ran_at']}")
    L.append(f"mode: {report['mode']}  device: {report['device']}")
    s = report["summary"]
    L.append(f"summary: entries={s['entries']} connected_or_ok={s['connected_or_ok']} pending_writes={s['pending_writes']} errors={s['errors']}")
    L.append("")
    for e in report["entries"]:
        kind = e.get("kind")
        name = e.get("name")
        state = e.get("state")
        err = e.get("error")
        if kind == "mcp-config":
            if err:
                L.append(f"[ERROR] {name}: {err}")
            elif e.get("applied") and e.get("action") in ("write", "overwrite"):
                L.append(f"[WROTE] {name}: {e['path']}")
            elif state == "connected":
                L.append(f"[OK   ] {name}: MCP server configured")
            elif state == "missing":
                L.append(f"[PLAN ] {name}: add MCP server to {e['path']}")
            elif state == "drifted":
                L.append(f"[PLAN ] {name}: replace existing agent-continuity MCP entry in {e['path']}")
            if e.get("backup_path"):
                L.append(f"        backup: {e['backup_path']}")
        elif kind == "thin-skills":
            rc = e.get("returncode")
            agents = "/".join(e.get("agents") or ["?"])
            L.append(f"[{('OK' if e.get('state') == 'ok' else 'PLAN' if e.get('state') == 'pending' else 'ERROR'):5}] {e.get('name')}: install {agents} skill pointer (rc={rc})")
            report_payload = e.get("report") or {}
            summary = report_payload.get("summary") if isinstance(report_payload, dict) else None
            if summary:
                L.append(f"        installer summary: {summary}")
            if e.get("stderr"):
                L.append(f"        stderr: {str(e['stderr']).strip()}")
        elif kind == "bridge-status":
            label = "OK" if state == "connected" else "INFO"
            L.append(f"[{label:5}] openclaw-bridge: mjs={e['agent_worker_mjs_present']} extension={e['extension_index_present']}")
            L.append(f"        {e['note']}")
        L.append("")
    if report["mode"] == "dry-run" and s["pending_writes"]:
        L.append("dry-run: rerun with `agent-continuity connect all --apply` to write MCP configs and skills.")
    return "\n".join(L).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect local adapters to agent-continuity. Dry-run by default.")
    parser.add_argument("target", nargs="?", default="all", choices=TARGET_CHOICES, help="what to inspect/connect")
    parser.add_argument("--apply", action="store_true", help="write configs/backups (default: dry-run)")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--human", action="store_true", help="print human summary only")
    args = parser.parse_args()

    if args.json and args.human:
        print("error: --json and --human are mutually exclusive", file=sys.stderr)
        return 64
    target = "all" if args.target == "doctor" else args.target
    report = build_report(apply=args.apply, include=[target])
    if not args.human:
        print(json.dumps(report, indent=2))
    if not args.json:
        if not args.human:
            print("\n---\n")
        print(render_human(report), end="")
    errors = report["summary"]["errors"]
    pending = report["summary"]["pending_writes"]
    if errors:
        return 1
    if pending and not args.apply:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
