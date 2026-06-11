#!/usr/bin/env bash
# team.sh — team-manifest management (v0.5.0+).
#
# Thin bash wrapper. All logic lives in _team.py.
#
# Subcommands:
#   init [--team-id ID] [--team-name NAME] [--admin-name NAME] [--force]
#                       create the manifest with the local device key as
#                       founding admin
#   show [--json]       display the current manifest
#   add-actor --human-actor-id ID --pubkey-file PATH
#             [--display-name NAME] [--device-label LABEL] [--as-admin]
#                       add an actor + device key (admin only)
#   verify              re-verify manifest signature against current admins
#
# Memory repo path: pass --path or set AGENT_CONTINUITY_MEMORY_REPO env.
# Local device key required (generate with `agent-continuity key generate`).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_team.py" "$@"
