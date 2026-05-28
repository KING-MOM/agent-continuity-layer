#!/usr/bin/env python3
"""install-thin-skills.py — install agent-continuity SKILL.md into each agent home.

Dry-run by default. --apply to write. Never overwrites without backup.
"""

from __future__ import annotations
import argparse
import difflib
import hashlib
import json
import os
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
INSTALL_VERSION = "1.0"

# Canonical install targets — one per agent, unified name "agent-continuity".
TARGETS: dict[str, Path] = {
    "claude":   HOME / ".claude" / "skills" / "agent-continuity" / "SKILL.md",
    "codex":    HOME / ".codex" / "skills" / "agent-continuity" / "SKILL.md",
    "openclaw": HOME / ".openclaw" / "workspace" / "skills" / "agent-continuity" / "SKILL.md",
}

# Agent home roots — we refuse to create these if missing (don't presume).
AGENT_HOME_ROOTS: dict[str, Path] = {
    "claude":   HOME / ".claude",
    "codex":    HOME / ".codex",
    "openclaw": HOME / ".openclaw",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_filesafe() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    lines = text.split("\n")
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    fm: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            k, _, v = line.partition(":")
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in '"\'':
                v = v[1:-1]
            fm[k.strip()] = v
    return fm


def _parse_version(v: str | None) -> tuple[int, ...] | None:
    if not v:
        return None
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return None


def _diff(old: str, new: str, old_label: str, new_label: str, max_lines: int = 80) -> str:
    lines = list(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=old_label,
        tofile=new_label,
        n=3,
    ))
    if len(lines) > max_lines:
        head = lines[:max_lines]
        return "".join(head) + f"... ({len(lines) - max_lines} more diff lines truncated)\n"
    return "".join(lines)


def _check_symlink_safety(target: Path, force_symlink: bool) -> str | None:
    """Return None if writing to `target` is safe, else an error string.

    Checks two paths: target itself and its immediate parent dir. If either is
    a symlink, refuses to write through it unless force_symlink is True. We
    don't walk higher than the immediate parent — at some point the operator
    owns their layout (e.g. ~/.claude itself being symlinked is their call)."""
    if force_symlink:
        return None
    if target.is_symlink():
        try:
            link_target = os.readlink(target)
        except OSError:
            link_target = "?"
        return (
            f"target {target} is a symlink to '{link_target}'; refusing to "
            f"write through it. Pass --force-symlink to override (then the "
            f"file the symlink points at will be the one modified, not this path)."
        )
    if target.parent.exists() and target.parent.is_symlink():
        try:
            link_target = os.readlink(target.parent)
        except OSError:
            link_target = "?"
        return (
            f"target parent {target.parent} is a symlink to '{link_target}'; "
            f"refusing to mkdir/write through it. Pass --force-symlink to override."
        )
    return None


def plan(agent: str, source: Path, target: Path) -> dict[str, Any]:
    """Compute one plan entry. Read-only — never mutates."""
    out: dict[str, Any] = {
        "agent": agent,
        "source": str(source),
        "target": str(target),
        "source_hash": None,
        "source_version": None,
        "installed_hash": None,
        "installed_version": None,
        "state": None,
        "action": None,
        "diff_unified": None,
        "backup_path": None,
        "applied": False,
        "error": None,
    }

    if not source.exists():
        out["state"] = "error"
        out["action"] = "skip-error"
        out["error"] = f"source missing in repo: {source}"
        return out

    src_text = source.read_text()
    out["source_hash"] = _sha256(source)
    out["source_version"] = _parse_frontmatter(src_text).get("version")

    root = AGENT_HOME_ROOTS[agent]
    if not root.is_dir():
        out["state"] = "error"
        out["action"] = "skip-error"
        out["error"] = f"agent home missing: {root} (refusing to create)"
        return out

    if not target.exists():
        out["state"] = "missing"
        out["action"] = "install"
        return out

    # Surface symlink status in the plan for visibility (informational; the
    # actual refusal happens at execute time, gated by --force-symlink).
    if target.is_symlink():
        out["target_is_symlink"] = True
        try:
            out["target_symlink_to"] = os.readlink(target)
        except OSError:
            pass
    if target.parent.exists() and target.parent.is_symlink():
        out["parent_is_symlink"] = True
        try:
            out["parent_symlink_to"] = os.readlink(target.parent)
        except OSError:
            pass

    inst_text = target.read_text()
    out["installed_hash"] = _sha256(target)
    out["installed_version"] = _parse_frontmatter(inst_text).get("version")

    if out["installed_hash"] == out["source_hash"]:
        out["state"] = "exact"
        out["action"] = "skip-exact"
        return out

    sv = _parse_version(out["source_version"])
    iv = _parse_version(out["installed_version"])
    if sv and iv and iv > sv:
        out["state"] = "newer"
        out["action"] = "skip-newer-requires-force"
    elif sv and iv and iv < sv:
        out["state"] = "older"
        out["action"] = "overwrite-older"
    else:
        out["state"] = "drifted"
        out["action"] = "overwrite-drifted"

    # Include unified diff for review.
    out["diff_unified"] = _diff(
        inst_text, src_text,
        f"installed/{agent}/SKILL.md v{out['installed_version'] or '?'}",
        f"repo/{agent}/SKILL.md v{out['source_version'] or '?'}",
    )
    return out


def execute(p: dict[str, Any], force: bool, force_symlink: bool = False) -> dict[str, Any]:
    """Apply one plan entry. Mutates filesystem. No-op for skip actions."""
    action = p["action"]
    target = Path(p["target"])
    source = Path(p["source"])

    if action == "skip-exact":
        p["applied"] = True
        return p
    if action == "skip-error":
        return p  # error already set
    if action == "skip-newer-requires-force" and not force:
        return p

    # Symlink-safety check before any filesystem mutation. Refusal here is
    # treated like any other error — the plan stays unapplied with an error
    # field, no backup, no write.
    sym_err = _check_symlink_safety(target, force_symlink)
    if sym_err:
        p["error"] = sym_err
        return p

    # All write paths from here.
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        p["error"] = f"could not mkdir {target.parent}: {e}"
        return p

    if target.exists():
        backup = target.parent / f"{target.name}.bak-{_now_filesafe()}"
        try:
            shutil.copy2(target, backup)
            p["backup_path"] = str(backup)
        except Exception as e:
            p["error"] = f"backup failed: {e}"
            return p

    # Atomic write: tmp + rename.
    tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
    try:
        tmp.write_bytes(source.read_bytes())
        os.replace(tmp, target)
        p["applied"] = True
    except Exception as e:
        p["error"] = f"write failed: {e}"
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    return p


def summarize(plans: list[dict[str, Any]]) -> dict[str, Any]:
    by_action: dict[str, int] = {}
    applied = 0
    errors = 0
    for p in plans:
        a = p["action"] or "unknown"
        by_action[a] = by_action.get(a, 0) + 1
        if p.get("applied"):
            applied += 1
        if p.get("error"):
            errors += 1
    requires_apply = sum(
        1 for p in plans
        if p["action"] not in ("skip-exact", "skip-error", "skip-newer-requires-force")
        and not p["applied"]
    )
    requires_force = sum(
        1 for p in plans
        if p["action"] == "skip-newer-requires-force" and not p["applied"]
    )
    return {
        "by_action": by_action,
        "applied": applied,
        "errors": errors,
        "requires_apply": requires_apply,
        "requires_force": requires_force,
    }


def render_human(report: dict[str, Any]) -> str:
    L: list[str] = []
    mode = report["mode"]
    L.append(f"agent-continuity install-thin-skills v{report['version']} - {report['ran_at']}")
    L.append(f"mode: {mode}{' (--force)' if report.get('force') else ''}"
             f"{'  agent: ' + report['agent_filter'] if report.get('agent_filter') else ''}")
    s = report["summary"]
    L.append(f"summary: {s['by_action']}  applied={s['applied']}  errors={s['errors']}  "
             f"requires_apply={s['requires_apply']}  requires_force={s['requires_force']}")
    L.append("")

    for p in report["plans"]:
        agent = p["agent"]
        state = p["state"]
        action = p["action"]
        sv = p.get("source_version") or "?"
        iv = p.get("installed_version") or "?"

        if action == "skip-error":
            L.append(f"[ERROR    ] {agent} ({state})")
            L.append(f"             {p.get('error','')}")
        elif action == "skip-exact":
            verb = "[MATCHED  ]" if mode == "apply" else "[SKIP-EXACT]"
            L.append(f"{verb} {agent} (already matches v{iv})")
        elif action == "install":
            verb = "[INSTALLED]" if (mode == "apply" and p["applied"]) else "[WOULD-INSTALL]"
            L.append(f"{verb} {agent} (missing → v{sv})")
            L.append(f"             source: {p['source']}")
            L.append(f"             target: {p['target']}")
        elif action == "overwrite-older":
            verb = "[OVERWROTE]" if (mode == "apply" and p["applied"]) else "[WOULD-OVERWRITE]"
            L.append(f"{verb} {agent} (older v{iv} → v{sv})")
            L.append(f"             target: {p['target']}")
            if p.get("backup_path"):
                L.append(f"             backup: {p['backup_path']}")
        elif action == "overwrite-drifted":
            verb = "[OVERWROTE]" if (mode == "apply" and p["applied"]) else "[WOULD-OVERWRITE]"
            L.append(f"{verb} {agent} (drifted v{iv}, hash differs → v{sv})")
            L.append(f"             target: {p['target']}")
            if p.get("backup_path"):
                L.append(f"             backup: {p['backup_path']}")
        elif action == "skip-newer-requires-force":
            if mode == "apply" and not report.get("force"):
                verb = "[NEEDS --force]"
            elif mode == "apply" and report.get("force") and p["applied"]:
                verb = "[FORCED   ]"
            else:
                verb = "[SKIP-NEWER]"
            L.append(f"{verb} {agent} (installed v{iv} > source v{sv})")
            L.append(f"             target: {p['target']}")
            L.append(f"             rerun with --apply --force to downgrade")
        else:
            L.append(f"[?{action}] {agent}")

        if p.get("error") and action != "skip-error":
            L.append(f"             error: {p['error']}")

        if p.get("diff_unified") and mode == "dry-run":
            L.append("             --- unified diff (installed vs source) ---")
            for line in p["diff_unified"].splitlines():
                L.append(f"             {line}")
        L.append("")

    if mode == "dry-run" and s["requires_apply"] > 0:
        L.append(f"dry-run: {s['requires_apply']} action(s) require --apply")
    if s["requires_force"] > 0:
        L.append(f"note: {s['requires_force']} action(s) require --force (would downgrade)")
    L.append("")
    L.append("(default mode is dry-run; --apply writes; --force overrides downgrade refusal)")
    return "\n".join(L)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install agent-continuity SKILL.md into each agent home. Dry-run by default.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually write (default: dry-run)")
    parser.add_argument("--force", action="store_true", help="Required to overwrite a target newer than source")
    parser.add_argument("--force-symlink", action="store_true",
                        help="Required to write through a symlinked target or parent directory (rare; off by default)")
    parser.add_argument("--agent", choices=list(TARGETS.keys()), help="Limit to one agent")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--human", action="store_true", help="Print human summary only")
    args = parser.parse_args()

    if args.json and args.human:
        print("error: --json and --human are mutually exclusive", file=sys.stderr)
        return 64

    agents = [args.agent] if args.agent else list(TARGETS.keys())
    plans: list[dict[str, Any]] = []
    for agent in agents:
        target = TARGETS[agent]
        source = REPO_ROOT / "skills" / agent / "SKILL.md"
        p = plan(agent, source, target)
        if args.apply:
            execute(p, args.force, args.force_symlink)
        plans.append(p)

    report = {
        "version": INSTALL_VERSION,
        "ran_at": _now(),
        "device": socket.gethostname(),
        "mode": "apply" if args.apply else "dry-run",
        "force": args.force,
        "agent_filter": args.agent,
        "plans": plans,
        "summary": summarize(plans),
    }

    if not args.human:
        print(json.dumps(report, indent=2))
    if not args.json:
        if not args.human:
            print()
            print("---")
            print()
        print(render_human(report))

    s = report["summary"]
    if s["errors"] > 0:
        return 1
    if s.get("requires_apply", 0) > 0 or s.get("requires_force", 0) > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
