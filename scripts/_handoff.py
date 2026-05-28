#!/usr/bin/env python3
"""_handoff.py — M16.0 device-to-device handoff (export / import / inspect).

Continuity primitive: handoff ledger (extended from worker-task handoff
to device-state handoff).

Packages the operator's local substrate state — and optionally their
Claude Code session transcripts — into a single tar.gz that another
device can ingest. The point is to close the UX gap where moving from
machine A to machine B requires manual rsync of multiple XDG paths.

Subcommands:
  export   build a handoff bundle from this device
  import   write a handoff bundle's contents into this device's state
           (after backing up whatever was there)
  inspect  print the manifest without extracting (preview before import)

Defaults are conservative:
  - export includes agent-continuity state (~/.config/agent-continuity,
    ~/.local/state/agent-continuity, ~/.cache/agent-continuity/queue)
    by default
  - export does NOT include Claude Code transcripts by default; use
    --include-claude to opt in (they can be hundreds of MB and contain
    sensitive content)
  - import always backs up existing target state to
    ~/.local/share/agent-continuity-handoff-backup-<TIMESTAMP>/
    unless --no-backup is passed
  - import refuses to restore Claude sessions when the target HOME
    path doesn't match the source HOME path (path-encoded directory
    names in ~/.claude/projects/ break across users). Transcripts are
    left in the tarball for manual extraction; M16.x can add rewrite
    support if a real use case surfaces.

Format inside the tarball:
  handoff/
    manifest.json
    agent-continuity/
      config/...        mirror of ~/.config/agent-continuity/
      state/...         mirror of ~/.local/state/agent-continuity/
      cache-queue/...   mirror of ~/.cache/agent-continuity/queue/
    claude/
      projects/...      mirror of ~/.claude/projects/ (when opted in)

Manifest is JSON declaring what's included, source identity, substrate
version, and creation timestamp. The import path validates the manifest
before touching anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import pathlib
import shutil
import socket
import sys
import tarfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "1.0"


# ────────────────────────────────────────────────────────────────
# Path resolution

def _xdg(env_var: str, fallback_rel: str) -> pathlib.Path:
    val = os.environ.get(env_var)
    if val:
        return pathlib.Path(val)
    return pathlib.Path.home() / fallback_rel


def _continuity_paths() -> dict[str, pathlib.Path]:
    """Return the three XDG roots the substrate writes to."""
    return {
        "config": _xdg("XDG_CONFIG_HOME", ".config") / "agent-continuity",
        "state": _xdg("XDG_STATE_HOME", ".local/state") / "agent-continuity",
        "cache-queue": _xdg("XDG_CACHE_HOME", ".cache") / "agent-continuity" / "queue",
    }


def _claude_projects_dir() -> pathlib.Path:
    """~/.claude/projects/ — Claude Code session storage location."""
    return pathlib.Path.home() / ".claude" / "projects"


def _substrate_version() -> str:
    try:
        return (REPO_ROOT / "core" / "VERSION").read_text().strip().splitlines()[0]
    except OSError:
        return "0.0.0"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ────────────────────────────────────────────────────────────────
# export

def _add_tree_to_tar(tar: tarfile.TarFile, source_dir: pathlib.Path, arc_prefix: str) -> int:
    """Walk source_dir and add every regular file under arc_prefix in the tar.
    Returns the count of files added."""
    if not source_dir.is_dir():
        return 0
    count = 0
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(source_dir)
        arcname = f"{arc_prefix}/{rel}"
        tar.add(str(path), arcname=arcname, recursive=False)
        count += 1
    return count


def cmd_export(args: argparse.Namespace) -> int:
    out_path = pathlib.Path(args.to).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    paths = _continuity_paths()
    include_state = not args.no_state
    include_claude = args.include_claude

    if not include_state and not include_claude:
        print(
            "error: nothing to export — pass --include-claude or remove --no-state",
            file=sys.stderr,
        )
        return 64

    counts = {
        "config": 0,
        "state": 0,
        "cache-queue": 0,
        "claude": 0,
    }

    with tarfile.open(out_path, "w:gz") as tar:
        # Manifest gets added LAST so we can populate counts. Stage the
        # body first, capture counts, then prepend manifest.
        if include_state:
            for label, src in paths.items():
                counts[label] = _add_tree_to_tar(
                    tar, src, f"handoff/agent-continuity/{label}"
                )

        if include_claude:
            claude = _claude_projects_dir()
            counts["claude"] = _add_tree_to_tar(
                tar, claude, "handoff/claude/projects"
            )
            if counts["claude"] == 0:
                print(
                    "warn: --include-claude was set but ~/.claude/projects/ is empty or missing",
                    file=sys.stderr,
                )

        # Build manifest with the final counts.
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "created_at": _now(),
            "source": {
                "device_hostname": socket.gethostname(),
                "home": str(pathlib.Path.home()),
                "substrate_version": _substrate_version(),
            },
            "included": {
                "agent_continuity_config": counts["config"] > 0,
                "agent_continuity_state": counts["state"] > 0,
                "agent_continuity_queue": counts["cache-queue"] > 0,
                "claude_sessions": counts["claude"] > 0,
            },
            "file_counts": counts,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        info = tarfile.TarInfo(name="handoff/manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(dt.datetime.now(dt.timezone.utc).timestamp())
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(manifest_bytes))

    size = out_path.stat().st_size
    print(f"exported handoff bundle: {out_path}")
    print(f"  size:    {size:,} bytes")
    print(f"  source:  {manifest['source']['device_hostname']} ({manifest['source']['home']})")
    print(f"  version: {manifest['source']['substrate_version']}")
    print(f"  files:")
    for label, n in counts.items():
        if n > 0:
            print(f"    {label}: {n}")
    return 0


# ────────────────────────────────────────────────────────────────
# inspect

def _read_manifest(bundle_path: pathlib.Path) -> dict | None:
    try:
        with tarfile.open(bundle_path, "r:gz") as tar:
            try:
                mf_member = tar.getmember("handoff/manifest.json")
            except KeyError:
                return None
            fh = tar.extractfile(mf_member)
            if fh is None:
                return None
            return json.loads(fh.read().decode("utf-8"))
    except (OSError, tarfile.TarError, json.JSONDecodeError):
        return None


def cmd_inspect(args: argparse.Namespace) -> int:
    bundle = pathlib.Path(args.bundle).resolve()
    if not bundle.is_file():
        print(f"error: bundle not found: {bundle}", file=sys.stderr)
        return 1
    mf = _read_manifest(bundle)
    if mf is None:
        print(f"error: bundle has no readable handoff/manifest.json", file=sys.stderr)
        return 1

    print(json.dumps(mf, indent=2, ensure_ascii=False))
    # Path-mismatch advisory
    source_home = mf.get("source", {}).get("home")
    target_home = str(pathlib.Path.home())
    if mf.get("included", {}).get("claude_sessions") and source_home != target_home:
        print(file=sys.stderr)
        print(
            f"NOTE: bundle includes Claude sessions, source home is {source_home!r}, "
            f"this device's home is {target_home!r}. Claude session restoration will "
            f"be SKIPPED on import — path-encoded directory names in ~/.claude/projects/ "
            f"do not survive cross-user transfer in M16.0.",
            file=sys.stderr,
        )
    return 0


# ────────────────────────────────────────────────────────────────
# import

def _backup_existing(paths: dict[str, pathlib.Path], backup_root: pathlib.Path) -> list[str]:
    """Move existing target paths into a backup dir. Returns the list of
    labels that were actually backed up."""
    backed = []
    for label, src in paths.items():
        if src.exists():
            dest = backup_root / label
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            backed.append(label)
    return backed


def _extract_subtree(tar: tarfile.TarFile, arc_prefix: str, target_dir: pathlib.Path) -> int:
    """Extract all members under arc_prefix into target_dir."""
    count = 0
    prefix = arc_prefix.rstrip("/") + "/"
    for member in tar.getmembers():
        if not member.name.startswith(prefix):
            continue
        if not member.isfile():
            continue
        rel = member.name[len(prefix):]
        if not rel:
            continue
        dest = target_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        fh = tar.extractfile(member)
        if fh is None:
            continue
        data = fh.read()
        dest.write_bytes(data)
        if member.mode:
            dest.chmod(member.mode & 0o777)
        count += 1
    return count


def cmd_import(args: argparse.Namespace) -> int:
    bundle = pathlib.Path(args.bundle).resolve()
    if not bundle.is_file():
        print(f"error: bundle not found: {bundle}", file=sys.stderr)
        return 1

    mf = _read_manifest(bundle)
    if mf is None:
        print(f"error: bundle has no readable handoff/manifest.json", file=sys.stderr)
        return 1

    if mf.get("schema_version") != SCHEMA_VERSION:
        print(
            f"error: bundle schema_version {mf.get('schema_version')!r} != {SCHEMA_VERSION!r}; "
            f"refusing to import (a later import-capable substrate version may handle it)",
            file=sys.stderr,
        )
        return 2

    source_home = mf.get("source", {}).get("home")
    target_home = str(pathlib.Path.home())
    home_matches = source_home == target_home

    print(f"importing handoff bundle: {bundle.name}")
    print(f"  source:  {mf.get('source', {}).get('device_hostname')} ({source_home})")
    print(f"  target:  {socket.gethostname()} ({target_home})")
    print(f"  bundle substrate version: {mf.get('source', {}).get('substrate_version')}")
    print(f"  this device substrate version: {_substrate_version()}")
    print()

    paths = _continuity_paths()

    # Backup existing local state. The backup goes under XDG_DATA_HOME
    # so it lives alongside the install dir and is easy to inspect with
    # `agent-continuity doctor` paths.
    backup_root = None
    if not args.no_backup:
        existing = {label: p for label, p in paths.items() if p.exists()}
        if existing:
            backup_root = (
                _xdg("XDG_DATA_HOME", ".local/share")
                / f"agent-continuity-handoff-backup-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            )
            backup_root.mkdir(parents=True, exist_ok=True)
            backed = _backup_existing(paths, backup_root)
            print(f"backed up existing state to: {backup_root}")
            for label in backed:
                print(f"  - {label}")
            print()

    # Extract agent-continuity state
    extracted = {label: 0 for label in paths.keys()}
    skipped_claude = False
    claude_count = 0
    with tarfile.open(bundle, "r:gz") as tar:
        if mf.get("included", {}).get("agent_continuity_config"):
            extracted["config"] = _extract_subtree(
                tar, "handoff/agent-continuity/config", paths["config"]
            )
        if mf.get("included", {}).get("agent_continuity_state"):
            extracted["state"] = _extract_subtree(
                tar, "handoff/agent-continuity/state", paths["state"]
            )
        if mf.get("included", {}).get("agent_continuity_queue"):
            extracted["cache-queue"] = _extract_subtree(
                tar, "handoff/agent-continuity/cache-queue", paths["cache-queue"]
            )

        if mf.get("included", {}).get("claude_sessions"):
            if not home_matches:
                print(
                    f"SKIPPING Claude sessions: source home {source_home!r} != target home {target_home!r}.",
                    file=sys.stderr,
                )
                print(
                    f"  Path-encoded directory names in ~/.claude/projects/ would break.",
                    file=sys.stderr,
                )
                print(
                    f"  Extract manually: tar -xzf {bundle} handoff/claude/",
                    file=sys.stderr,
                )
                skipped_claude = True
            else:
                claude_target = pathlib.Path.home() / ".claude"
                claude_count = _extract_subtree(
                    tar, "handoff/claude", claude_target
                )

    print("imported:")
    for label, n in extracted.items():
        if n > 0:
            print(f"  {label}: {n} files")
    if claude_count > 0:
        print(f"  claude sessions: {claude_count} files")
    if skipped_claude:
        print(f"  claude sessions: SKIPPED (path mismatch)")
    print()
    if backup_root is not None:
        print(f"to restore the pre-import state: rm + mv {backup_root}/* to their XDG roots")
    return 0


# ────────────────────────────────────────────────────────────────
# CLI

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="handoff",
        description=(
            "M16.0 device-to-device handoff. Export this device's agent-continuity "
            "state (and optionally Claude Code transcripts) to a tar.gz that another "
            "device can import."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="export this device's state to a handoff bundle")
    pe.add_argument("--to", required=True, help="output tar.gz path")
    pe.add_argument(
        "--include-claude",
        action="store_true",
        help="also include Claude Code session transcripts from ~/.claude/projects/",
    )
    pe.add_argument(
        "--no-state",
        action="store_true",
        help="skip agent-continuity state (use with --include-claude for claude-only)",
    )
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("import", help="import a handoff bundle into this device's state")
    pi.add_argument("bundle", help="path to handoff .tar.gz")
    pi.add_argument(
        "--no-backup",
        action="store_true",
        help="skip backing up existing local state before import (dangerous)",
    )
    pi.set_defaults(func=cmd_import)

    pn = sub.add_parser("inspect", help="print a bundle's manifest without extracting")
    pn.add_argument("bundle", help="path to handoff .tar.gz")
    pn.set_defaults(func=cmd_inspect)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
