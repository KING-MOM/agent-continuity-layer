#!/usr/bin/env python3
"""_key.py — local device keypair management (v0.5.0+).

The substrate's per-device Ed25519 keypair lives at:

  $XDG_CONFIG_HOME/agent-continuity/device-key.json

(Default: ~/.config/agent-continuity/device-key.json)

The private key NEVER leaves the device. The public key is what other
actors verify signatures against; it's safe to publish in the team
manifest and any operator-shared documentation.

Subcommands:
  generate [--human-actor-id ID] [--device-label LABEL] [--force]
                                       create a new keypair (refuses to
                                       overwrite existing without --force)
  show                                 print device_key_id, human_actor_id,
                                       device_label, created_at, public key
  export-pubkey [--out PATH]           print the public key in PEM form
                                       (for inclusion in team manifest)
  rotate [--device-label LABEL]        retire the current key, generate
                                       a new one. The old key file is
                                       archived to device-key-rotated-<ts>.json
                                       so historical signatures stay verifiable
                                       against the archived key.

File format (device-key.json):
  {
    "schema_version": "1.0",
    "device_key_id": "device:fp:<32 hex chars>",
    "human_actor_id": "human:fp:<32 hex chars> | human:custom:label",
    "device_label": "<optional, e.g. mau-macbook-pro>",
    "algorithm": "ed25519",
    "private_key_pem": "...",
    "public_key_pem": "...",
    "created_at": "<ISO-8601 UTC>"
  }

The file is written with mode 0600.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Any

# Local crypto helpers — single point of contact with the cryptography library
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _crypto import (
    generate_keypair,
    private_key_to_pem,
    public_key_to_pem,
    private_key_from_pem,
    public_key_from_pem,
    device_key_id_from_pubkey,
    human_actor_id_from_device,
)

SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key_dir() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return pathlib.Path(xdg) / "agent-continuity"
    return pathlib.Path.home() / ".config" / "agent-continuity"


def _key_path() -> pathlib.Path:
    return _key_dir() / "device-key.json"


def load_device_key() -> dict[str, Any] | None:
    """Read the device-key file. Returns None if it doesn't exist.
    Raises if it exists but is malformed."""
    p = _key_path()
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported device-key schema_version: {data.get('schema_version')!r}"
        )
    required = {
        "device_key_id",
        "human_actor_id",
        "algorithm",
        "private_key_pem",
        "public_key_pem",
        "created_at",
    }
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"device-key file missing fields: {sorted(missing)}")
    return data


def _write_device_key(data: dict[str, Any]) -> None:
    p = _key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Restrict to owner read/write
    os.chmod(p, 0o600)


# ──────────────────────────────────────────────────────────────────
# Subcommands

def cmd_generate(args: argparse.Namespace) -> int:
    existing = None
    try:
        existing = load_device_key()
    except ValueError as e:
        print(f"warn: existing device-key file is malformed: {e}", file=sys.stderr)
        print("       use --force to overwrite", file=sys.stderr)
    if existing and not args.force:
        print(
            f"error: device key already exists at {_key_path()}\n"
            f"       device_key_id: {existing.get('device_key_id')}\n"
            f"       use --force to overwrite (the existing key will be lost)\n"
            f"       to rotate cleanly, use: agent-continuity key rotate",
            file=sys.stderr,
        )
        return 1

    priv, pub = generate_keypair()
    device_key_id = device_key_id_from_pubkey(pub)
    human_actor_id = args.human_actor_id or human_actor_id_from_device(device_key_id)

    data = {
        "schema_version": SCHEMA_VERSION,
        "device_key_id": device_key_id,
        "human_actor_id": human_actor_id,
        "device_label": args.device_label or "",
        "algorithm": "ed25519",
        "private_key_pem": private_key_to_pem(priv),
        "public_key_pem": public_key_to_pem(pub),
        "created_at": _now_iso(),
    }
    _write_device_key(data)

    print(f"generated device keypair at {_key_path()}")
    print(f"  device_key_id:  {device_key_id}")
    print(f"  human_actor_id: {human_actor_id}")
    if args.device_label:
        print(f"  device_label:   {args.device_label}")
    print(f"  created_at:     {data['created_at']}")
    print()
    print("public key (safe to share — for team-manifest):")
    print(data["public_key_pem"].rstrip())
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    try:
        data = load_device_key()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if data is None:
        print(
            f"no device key found at {_key_path()}\n"
            f"  generate one with: agent-continuity key generate",
            file=sys.stderr,
        )
        return 1

    if args.json:
        public_view = {k: v for k, v in data.items() if k != "private_key_pem"}
        print(json.dumps(public_view, indent=2, sort_keys=True))
        return 0

    print(f"device key:       {_key_path()}")
    print(f"  device_key_id:  {data['device_key_id']}")
    print(f"  human_actor_id: {data['human_actor_id']}")
    if data.get("device_label"):
        print(f"  device_label:   {data['device_label']}")
    print(f"  algorithm:      {data['algorithm']}")
    print(f"  created_at:     {data['created_at']}")
    print()
    print("public key:")
    print(data["public_key_pem"].rstrip())
    return 0


def cmd_export_pubkey(args: argparse.Namespace) -> int:
    try:
        data = load_device_key()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if data is None:
        print(f"no device key found at {_key_path()}", file=sys.stderr)
        return 1
    pem = data["public_key_pem"]
    if args.out:
        pathlib.Path(args.out).write_text(pem, encoding="utf-8")
        print(f"wrote public key to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(pem)
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    try:
        existing = load_device_key()
    except ValueError as e:
        print(f"error: existing device-key file is malformed: {e}", file=sys.stderr)
        return 1
    if existing is None:
        print(
            f"no existing key to rotate at {_key_path()}\n"
            f"  use `key generate` to create a new key from scratch",
            file=sys.stderr,
        )
        return 1

    # Archive the current key so historical signatures stay verifiable
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = _key_dir() / f"device-key-rotated-{stamp}.json"
    archive_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(archive_path, 0o600)
    print(f"archived current key to {archive_path}", file=sys.stderr)

    # Generate the replacement
    priv, pub = generate_keypair()
    device_key_id = device_key_id_from_pubkey(pub)
    # Preserve human_actor_id by default — rotation should not change the
    # human identity, only the device key behind it.
    human_actor_id = existing["human_actor_id"]
    data = {
        "schema_version": SCHEMA_VERSION,
        "device_key_id": device_key_id,
        "human_actor_id": human_actor_id,
        "device_label": args.device_label or existing.get("device_label", ""),
        "algorithm": "ed25519",
        "private_key_pem": private_key_to_pem(priv),
        "public_key_pem": public_key_to_pem(pub),
        "created_at": _now_iso(),
        "rotated_from_device_key_id": existing["device_key_id"],
    }
    _write_device_key(data)

    print(f"rotated to new device key: {device_key_id}")
    print(f"  human_actor_id preserved: {human_actor_id}")
    print(f"  old device_key_id archived: {existing['device_key_id']}")
    print()
    print("next step: update the team manifest with the new public key")
    print("  agent-continuity team add-device-key --device-key-id ...")
    print("  (then revoke the old device_key_id when ready)")
    return 0


# ──────────────────────────────────────────────────────────────────
# CLI

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="key",
        description="Local Ed25519 device keypair (v0.5.0+).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="create a new keypair")
    p_gen.add_argument("--human-actor-id", default=None,
                       help="explicit human identity (default: derived from device key)")
    p_gen.add_argument("--device-label", default=None,
                       help="optional human-readable device name (e.g. mau-macbook-pro)")
    p_gen.add_argument("--force", action="store_true",
                       help="overwrite an existing key (the old key is lost; "
                            "for clean rotation use `key rotate`)")
    p_gen.set_defaults(func=cmd_generate)

    p_show = sub.add_parser("show", help="print device key metadata + public key")
    p_show.add_argument("--json", action="store_true",
                        help="emit JSON (private key field is omitted)")
    p_show.set_defaults(func=cmd_show)

    p_exp = sub.add_parser("export-pubkey", help="print the public key in PEM form")
    p_exp.add_argument("--out", default=None,
                       help="write to a file instead of stdout")
    p_exp.set_defaults(func=cmd_export_pubkey)

    p_rot = sub.add_parser("rotate", help="generate a new key, archive the old one")
    p_rot.add_argument("--device-label", default=None,
                       help="override device label (default: preserve)")
    p_rot.set_defaults(func=cmd_rotate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
