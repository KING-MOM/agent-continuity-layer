#!/usr/bin/env bash
# doctor.sh — read-only health report for agent-continuity-layer. Never mutates.
# Implementation lives in _doctor.py; this is a thin wrapper.
#
# Usage:
#   doctor.sh            # JSON, separator, human summary
#   doctor.sh --json     # JSON only (for tooling)
#   doctor.sh --human    # human summary only
#
# Exit: 0 ok, 1 any errors, 2 warnings only, 64 usage error.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/_doctor.py" "$@"
