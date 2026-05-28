#!/usr/bin/env bash
# connect.sh — unified adapter connection planner/installer (M13.4).
#
# Goal: after install, connect every local host that can speak the substrate
# in one operator-mediated flow. Dry-run by default; --apply writes configs
# with backups.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_connect.py" "$@"
