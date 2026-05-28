#!/usr/bin/env python3
"""M12.3 migration runner for agent-continuity-layer.

Walks the user's continuity data from a current version to a target
version using per-edge migrations under core/migrations/. Each
migration file declares its from/to versions, the paths it touches,
and the apply function. The runner discovers, plans, and (optionally)
applies the chain.

Hard rules from M12.3 sign-off:
  * `--dry-run` prints the plan; never writes.
  * Apply refuses unless the plan phase produced a valid chain.
  * No automatic migration during install — install.sh does not
    invoke this script.
  * No schema bump in this slice — no marker file is written.
    `--from` defaults to substrate VERSION, so by default current
    equals target and no migrations apply.

See core/migrations/README.md for the per-file contract.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import re
import sys
from collections import deque
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "core" / "migrations"
VERSION_FILE = REPO_ROOT / "core" / "VERSION"

# Migration filename pattern: v0.1.0_to_v0.1.1.py
_FILE_RE = re.compile(r"^v(\d+\.\d+\.\d+)_to_v(\d+\.\d+\.\d+)\.py$")
# Bare semver pattern used for --from / --to validation.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _substrate_version() -> str:
    return VERSION_FILE.read_text().strip().splitlines()[0]


def _xdg_path(env_var: str, fallback_rel: str) -> pathlib.Path:
    """Resolve an XDG base + 'agent-continuity' suffix.

    Returns the path that ALL substrate writes are scoped under for
    that base. Migrations declare their affected paths relative to
    these roots.
    """
    val = os.environ.get(env_var)
    base = pathlib.Path(val) if val else pathlib.Path.home() / fallback_rel
    return base / "agent-continuity"


def _build_env() -> dict[str, pathlib.Path]:
    return {
        "config": _xdg_path("XDG_CONFIG_HOME", ".config"),
        "state": _xdg_path("XDG_STATE_HOME", ".local/state"),
        "cache": _xdg_path("XDG_CACHE_HOME", ".cache"),
        "data": _xdg_path("XDG_DATA_HOME", ".local/share"),
        "substrate": REPO_ROOT,
    }


def _discover() -> dict[tuple[str, str], pathlib.Path]:
    """Find core/migrations/v{X}_to_v{Y}.py files. Returns {(from, to): path}."""
    found: dict[tuple[str, str], pathlib.Path] = {}
    if not MIGRATIONS_DIR.is_dir():
        return found
    for p in sorted(MIGRATIONS_DIR.glob("v*_to_v*.py")):
        m = _FILE_RE.match(p.name)
        if not m:
            continue
        found[(m.group(1), m.group(2))] = p
    return found


def _load(path: pathlib.Path):
    """Import a migration file as an ad-hoc module."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plan(
    current: str, target: str
) -> tuple[list[tuple[str, str, pathlib.Path]], str | None]:
    """Build the migration chain from current to target.

    Returns (chain, error). chain is a list of (from, to, path)
    triples in apply order. Empty chain means current == target.
    error is non-None when no path exists from current to target
    or when target has no incoming edges in any known migration.
    """
    if current == target:
        return [], None

    edges = _discover()

    # BFS. Each visited node stores the edge that reached it so we
    # can reconstruct the chain after we find target.
    predecessors: dict[str, tuple[str, str, pathlib.Path]] = {}
    visited = {current}
    queue: deque[str] = deque([current])
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for (frm, to), path in edges.items():
            if frm == node and to not in visited:
                visited.add(to)
                predecessors[to] = (frm, to, path)
                queue.append(to)

    if target not in visited:
        known = sorted(edges.keys())
        if known:
            available = ", ".join(f"v{a}→v{b}" for a, b in known)
            return [], (
                f"no migration path from v{current} to v{target}; "
                f"available edges: {available}"
            )
        return [], (
            f"no migration path from v{current} to v{target}; "
            f"no migrations are defined in core/migrations/"
        )

    chain: list[tuple[str, str, pathlib.Path]] = []
    node = target
    while node != current:
        edge = predecessors[node]
        chain.append(edge)
        node = edge[0]
    chain.reverse()
    return chain, None


def _validate_version(label: str, value: str) -> str | None:
    if not _VERSION_RE.match(value):
        return f"--{label} must be a semver triple like 0.1.0 (got: {value!r})"
    return None


def cmd_run(args: argparse.Namespace) -> int:
    target = args.to_version or _substrate_version()
    current = args.from_version or _substrate_version()

    for label, value in (("from", current), ("to", target)):
        err = _validate_version(label, value)
        if err:
            print(f"error: {err}", file=sys.stderr)
            return 2

    print(f"current version: v{current}")
    print(f"target version:  v{target}")

    chain, err = _plan(current, target)
    if err is not None:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if not chain:
        # No-op path. Both --dry-run and apply report success and
        # change nothing — by design, since same-version invocations
        # are how `agent-continuity migrate` behaves on every
        # fresh install until a real migration ships.
        print("plan:            no migrations needed (current == target)")
        if args.dry_run:
            print("--dry-run:       no changes would be made.")
        else:
            print("applied:         no changes.")
        return 0

    env = _build_env()
    print(f"plan:            {len(chain)} migration(s)")
    for frm, to, path in chain:
        print(f"  v{frm} → v{to}  [{path.name}]")
        try:
            mod = _load(path)
        except Exception as e:
            print(f"    error: failed to load {path.name}: {e}", file=sys.stderr)
            return 2
        affected: list[Any] = []
        if hasattr(mod, "affected_paths"):
            try:
                affected = list(mod.affected_paths(env))
            except Exception as e:
                print(
                    f"    error: affected_paths() raised in {path.name}: {e}",
                    file=sys.stderr,
                )
                return 2
        for ap in affected:
            print(f"    affects: {ap}")

    if args.dry_run:
        print("--dry-run:       nothing applied.")
        return 0

    # Apply phase. We already validated the plan above; if a
    # migrate() raises here it's an in-flight failure, not a
    # planning failure. Surface the version pair so the operator
    # knows where they stopped.
    for frm, to, path in chain:
        print(f"applying v{frm} → v{to} …")
        try:
            mod = _load(path)
            if not hasattr(mod, "migrate"):
                print(
                    f"error: {path.name} missing migrate() function",
                    file=sys.stderr,
                )
                return 2
            mod.migrate(env, dry_run=False)
        except Exception as e:
            print(
                f"error: v{frm} → v{to} failed mid-apply: {e}",
                file=sys.stderr,
            )
            return 2

    print(f"applied:         {len(chain)} migration(s).")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="migrate",
        description=(
            "agent-continuity schema migration runner. "
            "Defaults --from and --to to the substrate's core/VERSION, "
            "which means a no-op plan on a fresh install."
        ),
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan but do not apply (read-only)",
    )
    ap.add_argument(
        "--from",
        dest="from_version",
        metavar="VERSION",
        help="current version (default: substrate VERSION)",
    )
    ap.add_argument(
        "--to",
        dest="to_version",
        metavar="VERSION",
        help="target version (default: substrate VERSION)",
    )
    args = ap.parse_args()
    sys.exit(cmd_run(args))


if __name__ == "__main__":
    main()
