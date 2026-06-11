#!/usr/bin/env bash
# key.sh — local Ed25519 device keypair management (v0.5.0+).
#
# Thin bash wrapper. All logic lives in _key.py.
#
# Subcommands:
#   generate [--human-actor-id ID] [--device-label LABEL] [--force]
#                         create a new keypair (refuses to overwrite without --force)
#   show [--json]         print device_key_id, human_actor_id, public key
#   export-pubkey [--out PATH]
#                         print the public key in PEM form for team-manifest
#   rotate [--device-label LABEL]
#                         retire current key, generate replacement; old key archived
#
# Keypair location: $XDG_CONFIG_HOME/agent-continuity/device-key.json (mode 0600).
# The private key never leaves the device.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_key.py" "$@"
