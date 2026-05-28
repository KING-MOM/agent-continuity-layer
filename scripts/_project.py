#!/usr/bin/env python3
"""_project.py — M14.0 local project registry CLI.

Continuity primitive: project registry (#7 in the charter).

Manages local-device registry entries at:
  $XDG_CONFIG_HOME/agent-continuity/projects/<uuid>.json

Each entry conforms to core/schemas/project-registry-entry.schema.json
and is sync-compatible with the existing M10.1 structured-merge model
(merge key per-repo is `origin`).

Subcommands:
  list                              JSON or human listing of all entries
  add [--path P] [--name N] [...]   register cwd or --path; idempotent on
                                    origin URL match
  info <uuid-or-name-substring>     show one entry
  remove <uuid-or-name-substring>   delete one entry (requires --yes)

Module-public helper:
  ensure_project_registered(cwd) -> dict | None
    Used by write-side operations (decisions add, worker enqueue,
    context write) to auto-register the current repo if it has a git
    remote. Returns the entry dict on registration/match, None when
    cwd is not a git repo (no auto-register for non-git paths;
    explicit `project add` is required there).

    On NEW registration, prints a single notice to stderr:
      [agent-continuity] registered new project: <uuid> (<name>)
    so the operator sees the side effect even though they didn't
    type `project add`. Memory primitive, not trust authority —
    fine to write silently to the registry; not fine to write
    silently without acknowledgment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import uuid as uuidlib
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "core" / "schemas" / "project-registry-entry.schema.json"
SCHEMA_VERSION = "1.0"

# Reuse the stdlib schema validator already in _doctor.py.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _doctor import _validate_against_schema


# ────────────────────────────────────────────────────────────────
# Storage

def _projects_dir() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "agent-continuity" / "projects"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_entry(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_all() -> list[tuple[pathlib.Path, dict[str, Any]]]:
    """Return [(path, entry)] for all entries in the registry. Skips
    files that fail to parse — they're handled by doctor."""
    out: list[tuple[pathlib.Path, dict[str, Any]]] = []
    d = _projects_dir()
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        e = _load_entry(p)
        if isinstance(e, dict):
            out.append((p, e))
    return out


def _write_entry(entry: dict[str, Any]) -> pathlib.Path:
    """Validate against schema, then write atomically (.tmp + rename)."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = _validate_against_schema(entry, schema)
    if errors:
        raise ValueError(f"entry fails schema validation: {'; '.join(errors[:5])}")
    d = _projects_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{entry['uuid']}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


# ────────────────────────────────────────────────────────────────
# Git-remote detection

def _git_origin(cwd: pathlib.Path) -> str | None:
    """Return the canonical git remote URL for cwd, or None if cwd is
    not in a git repo or has no `origin` remote."""
    try:
        p = subprocess.run(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if p.returncode != 0:
        return None
    url = p.stdout.strip()
    return url or None


def _git_top_level(cwd: pathlib.Path) -> pathlib.Path | None:
    """Return the path to the git top-level dir containing cwd, or None."""
    try:
        p = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if p.returncode != 0:
        return None
    return pathlib.Path(p.stdout.strip()) if p.stdout.strip() else None


# ────────────────────────────────────────────────────────────────
# Lookup

def _find_by_origin(origin: str) -> dict[str, Any] | None:
    for _, entry in _load_all():
        for repo in entry.get("repos", []):
            if repo.get("origin") == origin:
                return entry
    return None


def _find_by_identifier(identifier: str) -> list[dict[str, Any]]:
    """Find entries by exact UUID, or by case-insensitive substring of name.
    Returns a list — caller decides how to handle 0/1/many matches."""
    matches: list[dict[str, Any]] = []
    ident_low = identifier.lower()
    for _, entry in _load_all():
        if entry.get("uuid") == identifier:
            return [entry]  # exact UUID match short-circuits
        if ident_low in entry.get("name", "").lower():
            matches.append(entry)
    return matches


def _resolve_one(identifier: str) -> dict[str, Any]:
    """Find exactly one entry, or raise."""
    matches = _find_by_identifier(identifier)
    if not matches:
        raise SystemExit(f"error: no project matched {identifier!r}")
    if len(matches) > 1:
        names = ", ".join(f"{e.get('uuid')[:8]}:{e.get('name')}" for e in matches[:5])
        raise SystemExit(
            f"error: identifier {identifier!r} matched {len(matches)} projects: {names}"
        )
    return matches[0]


# ────────────────────────────────────────────────────────────────
# Construction

def _new_entry(name: str, *, description: str = "", tags: list[str] | None = None,
               origin: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "uuid": str(uuidlib.uuid4()),
        "name": name,
        "created_at": _now(),
        "last_active": _now(),
    }
    if description:
        entry["description"] = description
    if tags:
        entry["tags"] = list(tags)
    if origin:
        entry["repos"] = [{"origin": origin}]
    return entry


# ────────────────────────────────────────────────────────────────
# Module-public helper for write-side auto-register

def ensure_project_registered(cwd: pathlib.Path | str | None = None) -> dict[str, Any] | None:
    """Auto-register the project containing cwd, if cwd is in a git repo.

    Returns the entry (existing or newly created). Returns None when
    cwd is not in a git repo — explicit `project add` is required to
    register non-git paths.

    Side effects:
      - On NEW registration: writes <uuid>.json under the projects dir
        AND prints a single notice to stderr identifying the new entry.
      - On existing-match: no writes (M14.0 keeps last_active update
        out of scope to avoid touching state on every read-adjacent op).
    """
    cwd_path = pathlib.Path(cwd or pathlib.Path.cwd()).resolve()
    origin = _git_origin(cwd_path)
    if origin is None:
        return None
    existing = _find_by_origin(origin)
    if existing is not None:
        return existing
    # Derive a name from the git top-level dir's basename. Falls back to
    # cwd basename if rev-parse fails (very unusual).
    top = _git_top_level(cwd_path)
    name = (top or cwd_path).name or "project"
    entry = _new_entry(name, origin=origin)
    _write_entry(entry)
    print(
        f"[agent-continuity] registered new project: {entry['uuid'][:8]} ({entry['name']})",
        file=sys.stderr,
    )
    return entry


# ────────────────────────────────────────────────────────────────
# CLI subcommands

def cmd_list(args: argparse.Namespace) -> int:
    entries = [e for _, e in _load_all()]
    if args.json:
        print(json.dumps({"projects": entries}, indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print("(no projects registered)")
        return 0
    for e in entries:
        repos = e.get("repos", [])
        origins = [r.get("origin", "?") for r in repos]
        print(f"{e['uuid'][:8]}  {e['name']:30}  {','.join(origins) or '-'}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.path or pathlib.Path.cwd()).resolve()
    origin = _git_origin(path)

    if origin is not None:
        existing = _find_by_origin(origin)
        if existing is not None:
            print(f"already registered: {existing['uuid'][:8]} ({existing['name']})")
            print(f"  origin: {origin}")
            return 0

    name = args.name or path.name or "project"
    entry = _new_entry(
        name,
        description=args.description or "",
        tags=args.tags.split(",") if args.tags else None,
        origin=origin,
    )
    written = _write_entry(entry)
    print(f"registered: {entry['uuid']} ({entry['name']})")
    print(f"  file:   {written}")
    if origin:
        print(f"  origin: {origin}")
    else:
        print("  (no git remote — auto-register from this path will be skipped)")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    entry = _resolve_one(args.identifier)
    if args.json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    entry = _resolve_one(args.identifier)
    path = _projects_dir() / f"{entry['uuid']}.json"
    if not args.yes:
        print(f"would remove: {entry['uuid']} ({entry['name']})")
        print(f"  file: {path}")
        print("re-run with --yes to confirm")
        return 2
    if path.exists():
        path.unlink()
    print(f"removed: {entry['uuid']} ({entry['name']})")
    return 0


# ────────────────────────────────────────────────────────────────
# Entry point

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="project",
        description=(
            "M14.0 local project registry. Manages entries under "
            "$XDG_CONFIG_HOME/agent-continuity/projects/."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list registered projects")
    p_list.add_argument("--json", action="store_true", help="emit JSON")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="register a project (idempotent on origin)")
    p_add.add_argument("--path", help="repo path (default: cwd)")
    p_add.add_argument("--name", help="project name (default: basename of path)")
    p_add.add_argument("--description", default="", help="free-text description")
    p_add.add_argument("--tags", default="", help="comma-separated tags")
    p_add.set_defaults(func=cmd_add)

    p_info = sub.add_parser("info", help="show one entry")
    p_info.add_argument("identifier", help="UUID exact, or name substring")
    p_info.add_argument("--json", action="store_true", help="JSON output (default)")
    p_info.set_defaults(func=cmd_info)

    p_rm = sub.add_parser("remove", help="remove one entry")
    p_rm.add_argument("identifier", help="UUID exact, or name substring")
    p_rm.add_argument("--yes", action="store_true", help="confirm deletion")
    p_rm.set_defaults(func=cmd_remove)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
