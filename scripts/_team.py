#!/usr/bin/env python3
"""_team.py — team-manifest management (v0.5.0+).

The team manifest is the registry that binds human_actor_id values to
the device public keys that are currently authorized to sign on each
actor's behalf. It lives at:

  <memory-repo>/team-manifest.json

(memory-repo path is supplied via --path or AGENT_CONTINUITY_MEMORY_REPO env)

Phase 1a scope:
  - team init             create the manifest, register founding admin
  - team show             display the current manifest
  - team add-actor        add a new actor + their device key (admin only)
  - team verify           re-verify the manifest signature against the
                          founding admin's public key (or current admin set)

Deferred to later phases:
  - role assertions (members have implicit member role for now)
  - M-of-N admin multisig (default M=1, N=1)
  - manifest versioning history (single mutable file is fine for v0.5.0)
  - device key rotation in-manifest (use `team add-actor` again for a
    second device key on the same human_actor_id; manual revocation by
    editing the manifest with admin signature)

The manifest signature covers everything except the signature itself.
Any change to actors/admin_set/multisig requires re-signing by an admin.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import uuid
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _crypto import (
    canonical_json_bytes,
    private_key_from_pem,
    public_key_from_pem,
    sign_payload,
    verify_payload,
)
from _key import load_device_key

SCHEMA_VERSION = "1.0"
MANIFEST_FILENAME = "team-manifest.json"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_path(args: argparse.Namespace) -> pathlib.Path:
    """Resolve the memory repo path (where team-manifest.json lives)."""
    if args.path:
        return pathlib.Path(args.path).expanduser().resolve()
    env_path = os.environ.get("AGENT_CONTINUITY_MEMORY_REPO")
    if env_path:
        return pathlib.Path(env_path).expanduser().resolve()
    raise SystemExit(
        "error: no memory repo path supplied. Use --path or set "
        "AGENT_CONTINUITY_MEMORY_REPO env var."
    )


def _manifest_path(repo: pathlib.Path) -> pathlib.Path:
    return repo / MANIFEST_FILENAME


def _canonical_for_signing(manifest: dict[str, Any]) -> bytes:
    """Manifest bytes covered by manifest_signature. Excludes the signature
    field itself."""
    body = {k: v for k, v in manifest.items() if k != "manifest_signature"}
    return canonical_json_bytes(body)


def _sign_manifest(manifest: dict[str, Any], key: dict[str, Any]) -> str:
    """Sign with the supplied device key. Returns base64 signature."""
    priv = private_key_from_pem(key["private_key_pem"])
    payload = _canonical_for_signing(manifest)
    return sign_payload(priv, payload)


def _verify_manifest_signature(
    manifest: dict[str, Any],
    candidate_pubkeys: list[str],
) -> tuple[bool, str]:
    """Verify manifest_signature against any of the supplied pubkeys.
    Returns (verified, reason)."""
    sig = manifest.get("manifest_signature")
    if not sig:
        return False, "no-signature"
    payload = _canonical_for_signing(manifest)
    for pem in candidate_pubkeys:
        try:
            pub = public_key_from_pem(pem)
            if verify_payload(pub, payload, sig):
                return True, "verified"
        except Exception:  # noqa: BLE001
            continue
    return False, "signature-invalid"


def _admin_pubkeys(manifest: dict[str, Any]) -> list[str]:
    """Return PEM pubkeys for all device keys belonging to humans in admin_set."""
    admin_ids = set(manifest.get("admin_set", []))
    pubkeys: list[str] = []
    for actor in manifest.get("actors", []):
        if actor.get("human_actor_id") in admin_ids:
            for dk in actor.get("device_keys", []):
                if dk.get("public_key_pem"):
                    pubkeys.append(dk["public_key_pem"])
    return pubkeys


# ──────────────────────────────────────────────────────────────────
# Subcommands

def cmd_init(args: argparse.Namespace) -> int:
    repo = _resolve_path(args)
    mp = _manifest_path(repo)
    if mp.exists() and not args.force:
        print(f"error: team manifest already exists at {mp}", file=sys.stderr)
        print("       use --force to overwrite (this loses founding admin lineage)", file=sys.stderr)
        return 1

    key = load_device_key()
    if not key:
        print(
            "error: no local device key. Run `agent-continuity key generate` first.",
            file=sys.stderr,
        )
        return 1

    team_id = args.team_id or str(uuid.uuid4())
    actor_entry = {
        "human_actor_id": key["human_actor_id"],
        "display_name": args.admin_name or "",
        "device_keys": [
            {
                "device_key_id": key["device_key_id"],
                "public_key_pem": key["public_key_pem"],
                "device_label": key.get("device_label", ""),
                "added_at": _now_iso(),
            }
        ],
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "team_id": team_id,
        "team_name": args.team_name or "",
        "created_at": _now_iso(),
        "founding_admin_human_actor_id": key["human_actor_id"],
        "actors": [actor_entry],
        "admin_set": [key["human_actor_id"]],
        "multisig": {"M": 1, "N": 1},
        "manifest_version": 1,
    }
    manifest["manifest_signature"] = _sign_manifest(manifest, key)

    repo.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote team manifest: {mp}")
    print(f"  team_id:                {team_id}")
    print(f"  team_name:              {args.team_name or '(unset)'}")
    print(f"  founding_admin:         {key['human_actor_id']}")
    print(f"  manifest_signature:     {manifest['manifest_signature'][:32]}...")
    print()
    print("next: invite team members.")
    print("  Each member runs: agent-continuity key generate --human-actor-id <their-id>")
    print("  They send you their public key (`agent-continuity key export-pubkey`).")
    print(f"  You run: agent-continuity team add-actor --path {repo} \\")
    print(f"           --human-actor-id <their-id> --pubkey-file <their-pubkey.pem>")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    repo = _resolve_path(args)
    mp = _manifest_path(repo)
    if not mp.exists():
        print(f"error: no team manifest at {mp}", file=sys.stderr)
        return 1
    manifest = json.loads(mp.read_text(encoding="utf-8"))

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    print(f"team manifest:          {mp}")
    print(f"  team_id:              {manifest['team_id']}")
    print(f"  team_name:            {manifest.get('team_name') or '(unset)'}")
    print(f"  created_at:           {manifest['created_at']}")
    print(f"  founding_admin:       {manifest['founding_admin_human_actor_id']}")
    print(f"  manifest_version:     {manifest['manifest_version']}")
    print(f"  multisig:             M={manifest['multisig']['M']}, N={manifest['multisig']['N']}")
    print(f"  admin_set:            {manifest['admin_set']}")
    print(f"  actors:               {len(manifest['actors'])}")
    for actor in manifest["actors"]:
        dn = actor.get("display_name") or "(no display name)"
        print(f"    - {actor['human_actor_id']}  {dn}")
        for dk in actor.get("device_keys", []):
            label = dk.get("device_label") or "(unlabeled)"
            print(f"        device: {dk['device_key_id']}  ({label})")
    return 0


def cmd_add_actor(args: argparse.Namespace) -> int:
    repo = _resolve_path(args)
    mp = _manifest_path(repo)
    if not mp.exists():
        print(f"error: no team manifest at {mp}. Run `team init` first.", file=sys.stderr)
        return 1
    manifest = json.loads(mp.read_text(encoding="utf-8"))

    # Operator must be an admin to add actors. Phase 1a: identity is the
    # local device key; check it's in admin_set.
    admin_key = load_device_key()
    if not admin_key:
        print("error: no local device key. Cannot sign manifest update.", file=sys.stderr)
        return 1
    if admin_key["human_actor_id"] not in manifest.get("admin_set", []):
        print(
            f"error: local human_actor_id ({admin_key['human_actor_id']}) is not in "
            f"admin_set ({manifest['admin_set']}). Only admins can add actors.",
            file=sys.stderr,
        )
        return 1

    # Load the new actor's public key
    pubkey_pem = pathlib.Path(args.pubkey_file).read_text(encoding="utf-8")
    # Validate it parses
    try:
        public_key_from_pem(pubkey_pem)
    except Exception as e:  # noqa: BLE001
        print(f"error: --pubkey-file is not a valid PEM Ed25519 public key: {e}", file=sys.stderr)
        return 1

    # Derive device_key_id (fingerprint scheme matches _key.py)
    import hashlib
    h = hashlib.sha256(pubkey_pem.encode("ascii")).hexdigest()[:32]
    new_device_key_id = f"device:fp:{h}"

    device_entry = {
        "device_key_id": new_device_key_id,
        "public_key_pem": pubkey_pem,
        "device_label": args.device_label or "",
        "added_at": _now_iso(),
    }

    # If the actor already exists, add this device under them. Otherwise
    # create a new actor entry.
    existing_actor = None
    for actor in manifest["actors"]:
        if actor["human_actor_id"] == args.human_actor_id:
            existing_actor = actor
            break
    if existing_actor:
        # Idempotency: skip if device already present
        for dk in existing_actor.get("device_keys", []):
            if dk.get("device_key_id") == new_device_key_id:
                print(f"device {new_device_key_id} already present for {args.human_actor_id}; no change", file=sys.stderr)
                return 0
        existing_actor.setdefault("device_keys", []).append(device_entry)
        print(f"added device {new_device_key_id} to existing actor {args.human_actor_id}")
    else:
        manifest["actors"].append({
            "human_actor_id": args.human_actor_id,
            "display_name": args.display_name or "",
            "device_keys": [device_entry],
        })
        print(f"added new actor {args.human_actor_id} with device {new_device_key_id}")

    # Optionally make them an admin
    if args.as_admin and args.human_actor_id not in manifest["admin_set"]:
        manifest["admin_set"].append(args.human_actor_id)
        print(f"  also added to admin_set")

    # Bump version, re-sign
    manifest["manifest_version"] += 1
    manifest["manifest_signature"] = _sign_manifest(manifest, admin_key)

    mp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"updated manifest at {mp}")
    print(f"  manifest_version:   {manifest['manifest_version']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    repo = _resolve_path(args)
    mp = _manifest_path(repo)
    if not mp.exists():
        print(f"error: no team manifest at {mp}", file=sys.stderr)
        return 1
    manifest = json.loads(mp.read_text(encoding="utf-8"))

    # Verify against current admin set's pubkeys
    candidates = _admin_pubkeys(manifest)
    if not candidates:
        print("error: manifest has no admin device keys to verify against", file=sys.stderr)
        return 1
    verified, reason = _verify_manifest_signature(manifest, candidates)
    if verified:
        print(f"manifest signature: VERIFIED against current admin set ({len(candidates)} candidate keys)")
        return 0
    print(f"manifest signature: FAILED ({reason})", file=sys.stderr)
    return 1


# ──────────────────────────────────────────────────────────────────
# CLI

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="team",
        description="Team-manifest management (v0.5.0+). Binds human_actor_ids to device keys.",
    )
    parser.add_argument("--path", default=None,
                        help="path to the team's memory repo (default: $AGENT_CONTINUITY_MEMORY_REPO)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create the team manifest with local device key as founding admin")
    p_init.add_argument("--team-id", default=None, help="UUID for the team (default: auto-generated)")
    p_init.add_argument("--team-name", default=None, help="human-readable team name")
    p_init.add_argument("--admin-name", default=None, help="display name for the founding admin")
    p_init.add_argument("--force", action="store_true", help="overwrite existing manifest")
    p_init.set_defaults(func=cmd_init)

    p_show = sub.add_parser("show", help="display the current manifest")
    p_show.add_argument("--json", action="store_true", help="emit JSON")
    p_show.set_defaults(func=cmd_show)

    p_add = sub.add_parser("add-actor", help="add an actor + their device key (admin only)")
    p_add.add_argument("--human-actor-id", required=True, help="the new actor's stable identity")
    p_add.add_argument("--pubkey-file", required=True, help="path to the actor's PEM public key file")
    p_add.add_argument("--display-name", default=None, help="human-readable name")
    p_add.add_argument("--device-label", default=None, help="optional label for the device this key lives on")
    p_add.add_argument("--as-admin", action="store_true", help="also add to admin_set")
    p_add.set_defaults(func=cmd_add_actor)

    p_ver = sub.add_parser("verify", help="verify manifest signature against current admin set")
    p_ver.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
