#!/usr/bin/env python3
"""Migrate trust grants from agent-worker.mjs's policy file to the continuity
layer's host-side policy. Dry-run by default. M5.2b deliverable."""

from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path.home()
_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
MJS_POLICY = HOME / ".openclaw" / "workspace" / "worker-tasks" / "trust-policy.json"
LAYER_POLICY = _XDG_CONFIG_HOME / "agent-continuity" / "trust-policy.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_or_die(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        print(f"error: could not parse {p}: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate .mjs trust grants → continuity layer trust-policy.json (dry-run by default).",
    )
    parser.add_argument("--apply", action="store_true",
                        help="Actually write the host policy (default: dry-run)")
    parser.add_argument("--json", action="store_true", help="JSON output only")
    args = parser.parse_args()

    mjs = _load_or_die(MJS_POLICY)
    layer = _load_or_die(LAYER_POLICY)

    mjs_grants = mjs.get("grants", []) if isinstance(mjs, dict) else []
    layer_grants = layer.get("grants", []) if isinstance(layer, dict) else []
    layer_grant_ids = {g.get("grant_id") for g in layer_grants if isinstance(g, dict)}

    # Identify grants that don't yet exist host-side
    to_migrate = [g for g in mjs_grants if g.get("grant_id") not in layer_grant_ids]

    report = {
        "ran_at": _now(),
        "mode": "apply" if args.apply else "dry-run",
        "mjs_policy": str(MJS_POLICY),
        "mjs_policy_exists": MJS_POLICY.exists(),
        "mjs_grants_count": len(mjs_grants),
        "layer_policy": str(LAYER_POLICY),
        "layer_policy_exists": LAYER_POLICY.exists(),
        "layer_grants_count_before": len(layer_grants),
        "to_migrate_count": len(to_migrate),
        "to_migrate": [
            {"grant_id": g.get("grant_id"), "worker": g.get("worker"),
             "repo": g.get("repo"), "expires_at": g.get("expires_at")}
            for g in to_migrate
        ],
        "skipped_already_present": [
            g.get("grant_id") for g in mjs_grants if g.get("grant_id") in layer_grant_ids
        ],
    }

    if not args.apply:
        report["next_step"] = (
            "rerun with --apply to write. The host policy will be backed up "
            "to a .bak-<timestamp> file before any change."
        )
        if not args.json:
            print(json.dumps(report, indent=2))
            print()
            print("---")
            print()
            print(f"DRY RUN: would migrate {len(to_migrate)} grant(s) from "
                  f"{MJS_POLICY} into {LAYER_POLICY}.")
            print(f"  source grants:  {len(mjs_grants)}")
            print(f"  already present: {len(report['skipped_already_present'])}")
            print(f"  to migrate:      {len(to_migrate)}")
            print()
            print("Re-run with --apply to write.")
        else:
            print(json.dumps(report, indent=2))
        return 0 if to_migrate else 0  # 0 either way for dry-run

    # Apply path
    if not to_migrate:
        report["status"] = "nothing-to-do"
        if not args.json:
            print("nothing to migrate — host policy already has all .mjs grants.")
        else:
            print(json.dumps(report, indent=2))
        return 0

    # Ensure layer policy has the expected top-level structure
    if not isinstance(layer, dict):
        layer = {}
    layer.setdefault("schema_version", "1.0")
    layer.setdefault("grants", [])

    # Backup the existing host policy
    LAYER_POLICY.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if LAYER_POLICY.exists():
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bak = LAYER_POLICY.with_name(LAYER_POLICY.name + f".bak-{ts}")
        shutil.copy2(LAYER_POLICY, bak)
        report["backup_path"] = str(bak)

    # Append the new grants
    layer["grants"].extend(to_migrate)

    # Atomic write
    tmp = LAYER_POLICY.with_name(LAYER_POLICY.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(layer, indent=2) + "\n")
    os.replace(tmp, LAYER_POLICY)
    os.chmod(LAYER_POLICY, 0o600)

    report["status"] = "migrated"
    report["layer_grants_count_after"] = len(layer["grants"])

    if not args.json:
        print(f"migrated {len(to_migrate)} grant(s) into {LAYER_POLICY}")
        if "backup_path" in report:
            print(f"backup: {report['backup_path']}")
    else:
        print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
