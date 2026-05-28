#!/usr/bin/env python3
"""sync_artifact.py — M10.0 bidirectional sync for global memory artifacts.

Continuity primitive: project registry (multi-device coordination) +
history (sync metadata records what happened across devices).

Operator-invoked. Sync memory, not behavior:
  - Syncs ONLY: decisions.jsonl (M10.0). Future: project-registry (M10.1),
    context-pinned (M10.2).
  - NEVER syncs: scripts, schemas, skills, trust policy, worker queue,
    OpenClaw bridge state. The VM is a memory store, not a code/config
    authority. (Charter non-goal: "this does not make the VM a config
    authority.")

Subcommands:
  push --artifact decisions    push local decisions.jsonl to VM (merge)
  pull --artifact decisions    pull VM decisions.jsonl to local (merge)

Merge semantics for decisions:
  union of local + remote, deduped by sha256 `id` (M8.0's content-
  addressing makes this trivial — identical decision bodies produce
  identical ids; collapsing them is information-preserving). Sorted by
  `ts` ascending in the written output, matching M8.0's append order
  convention.

Backend:
  M10.0 fake-VM backend only: AGENT_CONTINUITY_VM_PATH env var points to
  a local directory representing the VM filesystem. Real SSH-pinned VM
  is M10.3 scope (gated on real-VM availability).

Path layout on the VM:
  Global state (e.g. decisions.jsonl) lives at $VM/state/...
  Project-scoped artifacts (M10.1+) live at $VM/sessions/{project_uuid}/...
  This separation avoids forcing global decisions into a fake project.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

# XDG-honoring paths. Tests sandbox via XDG_CONFIG_HOME / XDG_STATE_HOME.
_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
_XDG_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or (HOME / ".local" / "state"))

DEVICE_IDENTITY_PATH = _XDG_CONFIG_HOME / "agent-continuity" / "device-identity.json"
SYNC_METADATA_PATH = _XDG_CONFIG_HOME / "agent-continuity" / "sync-metadata.json"

# Local decisions log (per M8.0).
DECISIONS_PATH = _XDG_STATE_HOME / "agent-continuity" / "decisions.jsonl"

# M10.1: per-device per-project registry entries. Flat layout for now;
# moves to sessions/{uuid}/ tree if M-future grows multiple project-
# scoped files (decisions don't live here; this is registry only).
LOCAL_REGISTRY_DIR = _XDG_STATE_HOME / "agent-continuity" / "registry"

# M10.2 pin lives in the repo at core/context-pinned.json. AGENT_CONTINUITY_PIN_PATH
# env var overrides the path — load-bearing for two-fake-devices testing on a
# single machine where each "device" needs a distinct pin file. Production use
# leaves the env var unset and lands at the repo path.
LOCAL_PIN_PATH = Path(
    os.environ.get("AGENT_CONTINUITY_PIN_PATH")
    or (REPO_ROOT / "core" / "context-pinned.json")
)

# VM path layout per M10.0 sign-off: global state in $VM/state/...,
# project-scoped in $VM/sessions/{uuid}/...
VM_DECISIONS_REL = "state/decisions.jsonl"
VM_PIN_REL = "state/context-pinned.json"
VM_SESSIONS_DIR_REL = "sessions"
VM_REGISTRY_ENTRY_FILENAME = "registry-entry.json"

# Artifacts known to M10. Argparse uses this list as the choices set.
SUPPORTED_ARTIFACTS = ("decisions", "project-registry", "context-pinned")

# Reuse M8.0's decisions lock for safe concurrent access (decisions.sh add
# might fire between our read and write).
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _decisions import _acquire_lock, _release_lock  # noqa: E402


# ---------- helpers ----------

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _vm_root() -> Path | None:
    """Return the VM backend root path. M10.0 supports only the local
    fake-VM directory via AGENT_CONTINUITY_VM_PATH; SSH backend is M10.3."""
    v = os.environ.get("AGENT_CONTINUITY_VM_PATH")
    if v:
        return Path(v)
    return None


def _ensure_device_identity() -> dict[str, Any]:
    """Read existing identity, or generate + persist a new one. Identity
    is per-device + stable across runs."""
    if DEVICE_IDENTITY_PATH.exists():
        try:
            return json.loads(DEVICE_IDENTITY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"corrupt device identity at {DEVICE_IDENTITY_PATH}: {e}; "
                "fix or remove the file to regenerate"
            )
    DEVICE_IDENTITY_PATH.parent.mkdir(parents=True, exist_ok=True)
    hostname = socket.gethostname()
    device_id = f"{hostname}-{secrets.token_hex(4)}"
    identity = {
        "schema_version": "1.0",
        "device_id": device_id,
        "display_name": hostname,
        "hostname": hostname,
        "created_at": _now_iso(),
    }
    tmp = DEVICE_IDENTITY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, DEVICE_IDENTITY_PATH)
    return identity


def _load_sync_metadata(device_id: str) -> dict[str, Any]:
    if SYNC_METADATA_PATH.exists():
        try:
            data = json.loads(SYNC_METADATA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and data.get("device_id") == device_id:
            return data
    return {
        "schema_version": "1.0",
        "device_id": device_id,
        "artifacts": {},
    }


def _save_sync_metadata(meta: dict[str, Any]) -> None:
    SYNC_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SYNC_METADATA_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, SYNC_METADATA_PATH)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file as a list of dicts. Skips malformed lines silently
    (doctor's M8.1 check is the canonical place to surface log corruption;
    sync is not the place to report on it)."""
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def _atomic_write_jsonl(path: Path, entries: list[dict[str, Any]]) -> int:
    """Write entries as JSONL atomically (tmp + os.replace). Returns the
    byte count of the written content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if entries:
        content = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
    else:
        content = ""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return len(content.encode("utf-8"))


def _merge_decisions(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union + dedupe by `id`, sorted by `ts` ascending.

    M8.0's sha256-id property: identical decision bodies produce identical
    ids. Two devices independently writing the same logical decision get
    the same id and collapse to one entry on merge. Different decisions
    have different ids (ts differs at minimum) and both survive."""
    by_id: dict[str, dict[str, Any]] = {}
    for entry in a + b:
        eid = entry.get("id")
        if not isinstance(eid, str) or not eid:
            continue
        by_id[eid] = entry
    return sorted(by_id.values(), key=lambda e: e.get("ts", "") or "")


# ---------- registry helpers (M10.1) ----------

def _local_registry_path(uuid: str) -> Path:
    return LOCAL_REGISTRY_DIR / f"{uuid}.json"


def _vm_registry_path(vm_root: Path, uuid: str) -> Path:
    return vm_root / VM_SESSIONS_DIR_REL / uuid / VM_REGISTRY_ENTRY_FILENAME


def _read_registry_entry(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_write_json(path: Path, data: dict[str, Any]) -> int:
    """Write a JSON object atomically (tmp + os.replace). Returns byte count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return len(content.encode("utf-8"))


def _list_local_uuids() -> list[str]:
    if not LOCAL_REGISTRY_DIR.is_dir():
        return []
    return sorted(p.stem for p in LOCAL_REGISTRY_DIR.glob("*.json") if p.is_file())


def _list_vm_uuids(vm_root: Path) -> list[str]:
    sessions = vm_root / VM_SESSIONS_DIR_REL
    if not sessions.is_dir():
        return []
    out: list[str] = []
    for d in sorted(sessions.iterdir()):
        if d.is_dir() and (d / VM_REGISTRY_ENTRY_FILENAME).exists():
            out.append(d.name)
    return out


def _merge_registry_entry(
    local: dict[str, Any] | None,
    remote: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """M10.1 structured merge for a single project-registry-entry.

    Scalar fields: LWW by last_writer_ts; alphabetical last_writer_device
    tiebreak (deterministic, not load-bearing).

    repos[]: union by origin; observed_paths[] within each repo deduped
    by device_id (one preferred path per repo per device for M10.1 v1),
    LWW by observed_at within (origin, device_id).

    Per-device observations are FACTS, not opinions — the merge never
    erases one device's path observation because another device updated
    an unrelated scalar field. That's the deliberate deviation from
    pure whole-record LWW.

    Returns None only when both inputs are None. Otherwise returns a
    merged dict.
    """
    if local is None and remote is None:
        return None
    if local is None:
        return dict(remote)  # type: ignore[arg-type]
    if remote is None:
        return dict(local)

    l_ts = local.get("last_writer_ts") or ""
    r_ts = remote.get("last_writer_ts") or ""
    if r_ts > l_ts:
        winner = remote
    elif l_ts > r_ts:
        winner = local
    else:
        # Tie: alphabetical last_writer_device; lower wins
        l_dev = local.get("last_writer_device") or ""
        r_dev = remote.get("last_writer_device") or ""
        winner = remote if r_dev < l_dev else local

    # Start from winner's scalar fields (everything except repos[])
    merged: dict[str, Any] = {k: v for k, v in winner.items() if k != "repos"}

    # Union repos[] by origin; observed_paths[] dedup by device_id (LWW by observed_at)
    repos_by_origin: dict[str, dict[str, Any]] = {}
    for source in (local, remote):
        for repo in source.get("repos") or []:
            if not isinstance(repo, dict):
                continue
            origin = repo.get("origin")
            if not isinstance(origin, str) or not origin:
                continue
            if origin not in repos_by_origin:
                repos_by_origin[origin] = {"origin": origin, "observed_paths": []}
            for op in repo.get("observed_paths") or []:
                if not isinstance(op, dict):
                    continue
                repos_by_origin[origin]["observed_paths"].append(op)

    for origin, repo in repos_by_origin.items():
        by_device: dict[str, dict[str, Any]] = {}
        for op in repo["observed_paths"]:
            dev = op.get("device_id")
            if not isinstance(dev, str) or not dev:
                continue
            existing = by_device.get(dev)
            if existing is None or (op.get("observed_at") or "") > (existing.get("observed_at") or ""):
                by_device[dev] = op
        repo["observed_paths"] = sorted(
            by_device.values(), key=lambda x: x.get("device_id") or ""
        )

    merged["repos"] = sorted(repos_by_origin.values(), key=lambda r: r.get("origin") or "")
    return merged


# ---------- context-pinned helpers (M10.2) ----------

def _read_pin(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _merge_pin(
    local: dict[str, Any] | None,
    remote: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """M10.2 LWW pin merge with attribution.

    Pure LWW comparison (no prose merge per sign-off: "no merge of prose"):
      - If both have last_writer_ts: newer wins; alphabetical
        last_writer_device tiebreak.
      - If only one has last_writer_ts: that side wins (the other is
        treated as 'ancient').
      - If neither has last_writer_ts: deterministic local-wins fallback
        (operator's local edit is sacred until they explicitly push).

    Push pre-stamps local with the current device + now() so local
    always carries an explicit ts going forward — see _push_pin.

    Returns None only when both inputs are None."""
    if local is None and remote is None:
        return None
    if local is None:
        return dict(remote)  # type: ignore[arg-type]
    if remote is None:
        return dict(local)

    l_ts = local.get("last_writer_ts") or ""
    r_ts = remote.get("last_writer_ts") or ""

    if l_ts and not r_ts:
        return dict(local)
    if r_ts and not l_ts:
        return dict(remote)
    if not l_ts and not r_ts:
        # Neither stamped. Keep local — operator's pin on this device
        # is sacred until they explicitly push.
        return dict(local)

    if l_ts > r_ts:
        return dict(local)
    if r_ts > l_ts:
        return dict(remote)

    # Tie: alphabetical device_id, lower wins (deterministic, not load-bearing)
    l_dev = local.get("last_writer_device") or ""
    r_dev = remote.get("last_writer_device") or ""
    return dict(remote) if r_dev < l_dev else dict(local)


# ---------- commands ----------

def _check_vm_root() -> Path | None:
    root = _vm_root()
    if root is None:
        print(
            "error: no VM backend configured. Set AGENT_CONTINUITY_VM_PATH "
            "to a local directory for fake-VM mode (M10.0). Real SSH-pinned "
            "VM support is M10.3 scope.",
            file=sys.stderr,
        )
        return None
    root.mkdir(parents=True, exist_ok=True)
    return root


def _push_decisions(device_id: str, vm_root: Path) -> tuple[int, dict[str, Any]]:
    vm_path = vm_root / VM_DECISIONS_REL

    # Read local decisions under M8.0's lock so a concurrent decisions.sh add
    # doesn't race us. Release before touching the VM — VM I/O can be slow,
    # and we don't want to block adders longer than necessary.
    try:
        _acquire_lock()
    except TimeoutError as e:
        return 1, {"error": f"could not acquire decisions lock: {e}"}
    try:
        local_entries = _read_jsonl(DECISIONS_PATH)
    finally:
        _release_lock()

    remote_entries = _read_jsonl(vm_path)
    merged = _merge_decisions(local_entries, remote_entries)
    bytes_pushed = _atomic_write_jsonl(vm_path, merged)

    return 0, {
        "cmd": "push",
        "artifact": "decisions",
        "device_id": device_id,
        "vm_path": str(vm_path),
        "local_count": len(local_entries),
        "remote_count_before": len(remote_entries),
        "merged_count": len(merged),
        "bytes_on_vm": bytes_pushed,
    }


def _pull_decisions(device_id: str, vm_root: Path) -> tuple[int, dict[str, Any]]:
    vm_path = vm_root / VM_DECISIONS_REL

    # Pull is more delicate: we read remote (no lock needed; VM is operator-
    # mediated in M10.0) and then we BOTH read AND write local under lock.
    remote_entries = _read_jsonl(vm_path)

    try:
        _acquire_lock()
    except TimeoutError as e:
        return 1, {"error": f"could not acquire decisions lock: {e}"}
    try:
        local_entries = _read_jsonl(DECISIONS_PATH)
        merged = _merge_decisions(local_entries, remote_entries)
        bytes_local = _atomic_write_jsonl(DECISIONS_PATH, merged)
    finally:
        _release_lock()

    return 0, {
        "cmd": "pull",
        "artifact": "decisions",
        "device_id": device_id,
        "vm_path": str(vm_path),
        "local_count_before": len(local_entries),
        "remote_count": len(remote_entries),
        "merged_count": len(merged),
        "bytes_local": bytes_local,
    }


def _push_registry(device_id: str, vm_root: Path) -> tuple[int, dict[str, Any]]:
    """Walk both local + VM registry trees; for every uuid present on
    either side, structured-merge and write to VM.

    M10.1 v1: no per-project lock. Atomic tmp+rename protects against
    torn reads. Operator-initiated sync at low frequency makes this
    acceptable per sign-off."""
    local_uuids = set(_list_local_uuids())
    vm_uuids = set(_list_vm_uuids(vm_root))
    all_uuids = sorted(local_uuids | vm_uuids)

    projects: list[dict[str, Any]] = []
    total_bytes = 0
    for uuid in all_uuids:
        local = _read_registry_entry(_local_registry_path(uuid))
        remote = _read_registry_entry(_vm_registry_path(vm_root, uuid))
        merged = _merge_registry_entry(local, remote)
        if merged is None:
            continue
        vm_path = _vm_registry_path(vm_root, uuid)
        written = _atomic_write_json(vm_path, merged)
        total_bytes += written
        projects.append({
            "uuid": uuid,
            "had_local": local is not None,
            "had_vm": remote is not None,
            "bytes_on_vm": written,
        })

    return 0, {
        "cmd": "push",
        "artifact": "project-registry",
        "device_id": device_id,
        "vm_sessions_dir": str(vm_root / VM_SESSIONS_DIR_REL),
        "projects": projects,
        "count": len(projects),
        "total_bytes_on_vm": total_bytes,
    }


def _pull_registry(device_id: str, vm_root: Path) -> tuple[int, dict[str, Any]]:
    """Mirror of _push_registry but writes to local. Same structured merge."""
    local_uuids = set(_list_local_uuids())
    vm_uuids = set(_list_vm_uuids(vm_root))
    all_uuids = sorted(local_uuids | vm_uuids)

    projects: list[dict[str, Any]] = []
    total_bytes = 0
    for uuid in all_uuids:
        local = _read_registry_entry(_local_registry_path(uuid))
        remote = _read_registry_entry(_vm_registry_path(vm_root, uuid))
        merged = _merge_registry_entry(local, remote)
        if merged is None:
            continue
        local_path = _local_registry_path(uuid)
        written = _atomic_write_json(local_path, merged)
        total_bytes += written
        projects.append({
            "uuid": uuid,
            "had_local": local is not None,
            "had_vm": remote is not None,
            "bytes_local": written,
        })

    return 0, {
        "cmd": "pull",
        "artifact": "project-registry",
        "device_id": device_id,
        "local_registry_dir": str(LOCAL_REGISTRY_DIR),
        "projects": projects,
        "count": len(projects),
        "total_bytes_local": total_bytes,
    }


def _push_pin(device_id: str, vm_root: Path) -> tuple[int, dict[str, Any]]:
    """Push local pin to VM with this-device attribution.

    On push, we always stamp local with (current_device_id, now()) BEFORE
    the LWW comparison. Result: the local copy + the VM copy both reflect
    'this device pushed this version at this time.' Operator edits made
    since the last push get attributed as a fresh write.

    The local pin file is also updated with the stamped version so the
    next pull doesn't see local as 'unstamped' and reverse the attribution."""
    local = _read_pin(LOCAL_PIN_PATH)
    if local is None:
        return 1, {"error": f"no local pin to push at {LOCAL_PIN_PATH}"}

    # Stamp local with current device + now BEFORE merge
    stamped_local = dict(local)
    stamped_local["last_writer_device"] = device_id
    stamped_local["last_writer_ts"] = _now_iso()

    vm_path = vm_root / VM_PIN_REL
    remote = _read_pin(vm_path)

    merged = _merge_pin(stamped_local, remote)
    if merged is None:
        return 1, {"error": "merge produced no entry — should not happen"}

    bytes_vm = _atomic_write_json(vm_path, merged)
    bytes_local = _atomic_write_json(LOCAL_PIN_PATH, merged)

    return 0, {
        "cmd": "push",
        "artifact": "context-pinned",
        "device_id": device_id,
        "vm_path": str(vm_path),
        "local_path": str(LOCAL_PIN_PATH),
        "last_writer_device": merged.get("last_writer_device"),
        "last_writer_ts": merged.get("last_writer_ts"),
        "bytes_on_vm": bytes_vm,
        "bytes_local": bytes_local,
    }


def _pull_pin(device_id: str, vm_root: Path) -> tuple[int, dict[str, Any]]:
    """Pull VM pin to local with LWW merge.

    Pull does NOT stamp — we preserve the writer attribution of whoever
    originally pushed. If local has unstamped operator edits, M10.2's
    fallback keeps them (local wins on no-ts pair); the doctor's sync
    check is the place that surfaces 'local has un-pushed edits.'"""
    vm_path = vm_root / VM_PIN_REL
    remote = _read_pin(vm_path)
    if remote is None:
        return 1, {"error": f"no pin on VM at {vm_path}"}

    local = _read_pin(LOCAL_PIN_PATH)
    merged = _merge_pin(local, remote)
    if merged is None:
        return 1, {"error": "merge produced no entry — should not happen"}

    bytes_local = _atomic_write_json(LOCAL_PIN_PATH, merged)

    return 0, {
        "cmd": "pull",
        "artifact": "context-pinned",
        "device_id": device_id,
        "vm_path": str(vm_path),
        "local_path": str(LOCAL_PIN_PATH),
        "last_writer_device": merged.get("last_writer_device"),
        "last_writer_ts": merged.get("last_writer_ts"),
        "bytes_local": bytes_local,
    }


def _update_sync_metadata(
    device_id: str, artifact: str, direction: str, report: dict[str, Any]
) -> None:
    meta = _load_sync_metadata(device_id)
    meta.setdefault("artifacts", {}).setdefault(artifact, {})
    if direction == "push":
        meta["artifacts"][artifact]["last_push_at"] = _now_iso()
        # Decisions report carries bytes_on_vm; registry report carries
        # total_bytes_on_vm. Fall through both shapes.
        bytes_val = report.get("bytes_on_vm")
        if bytes_val is None:
            bytes_val = report.get("total_bytes_on_vm", 0)
        meta["artifacts"][artifact]["last_push_bytes"] = bytes_val
    else:
        meta["artifacts"][artifact]["last_pull_at"] = _now_iso()
        bytes_val = report.get("bytes_local")
        if bytes_val is None:
            bytes_val = report.get("total_bytes_local", 0)
        meta["artifacts"][artifact]["last_pull_bytes"] = bytes_val
    _save_sync_metadata(meta)


def cmd_push(args: argparse.Namespace) -> int:
    vm_root = _check_vm_root()
    if vm_root is None:
        return 1

    identity = _ensure_device_identity()
    device_id = identity["device_id"]

    if args.artifact == "decisions":
        rc, report = _push_decisions(device_id, vm_root)
    elif args.artifact == "project-registry":
        rc, report = _push_registry(device_id, vm_root)
    elif args.artifact == "context-pinned":
        rc, report = _push_pin(device_id, vm_root)
    else:
        print(f"error: unsupported artifact {args.artifact!r}", file=sys.stderr)
        return 1

    if rc != 0:
        print(f"error: {report.get('error', 'push failed')}", file=sys.stderr)
        return rc

    _update_sync_metadata(device_id, args.artifact, "push", report)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    vm_root = _check_vm_root()
    if vm_root is None:
        return 1

    identity = _ensure_device_identity()
    device_id = identity["device_id"]

    if args.artifact == "decisions":
        rc, report = _pull_decisions(device_id, vm_root)
    elif args.artifact == "project-registry":
        rc, report = _pull_registry(device_id, vm_root)
    elif args.artifact == "context-pinned":
        rc, report = _pull_pin(device_id, vm_root)
    else:
        print(f"error: unsupported artifact {args.artifact!r}", file=sys.stderr)
        return 1

    if rc != 0:
        print(f"error: {report.get('error', 'pull failed')}", file=sys.stderr)
        return rc

    _update_sync_metadata(device_id, args.artifact, "pull", report)
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M10.0 multi-device sync for global memory artifacts. "
            "Sync memory, not behavior. See docs/roadmap.md M10."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("push", help="push local artifact to VM (merge into remote)")
    p.add_argument("--artifact", required=True, choices=list(SUPPORTED_ARTIFACTS),
                   help="M10.0: 'decisions'; M10.1: 'project-registry'")

    q = sub.add_parser("pull", help="pull VM artifact to local (merge into local)")
    q.add_argument("--artifact", required=True, choices=list(SUPPORTED_ARTIFACTS),
                   help="M10.0: 'decisions'; M10.1: 'project-registry'")

    args = parser.parse_args(argv)
    if args.cmd == "push":
        return cmd_push(args)
    if args.cmd == "pull":
        return cmd_pull(args)
    parser.print_help(sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
