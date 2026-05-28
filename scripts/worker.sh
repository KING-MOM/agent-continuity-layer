#!/usr/bin/env bash
# worker.sh — agent-continuity worker-task queue.
# Implementation lives in _worker.py; this is a thin wrapper.
#
# Subcommands:
#   enqueue   submit a new worker-task; validated against trust policy
#   list      show tasks grouped by state
#   show      print one task's full JSON + audit transitions
#   claim     move a queued task to claimed (re-verifies policy)
#   submit    record result on a claimed task -> completed | awaiting-approval | failed
#
# Output format: JSON + separator + human summary by default.
#   --json    JSON only (for tooling)
#   --human   human summary only
#
# Exit codes: 0 ok, 1 hard errors, 2 policy rejection / state mismatch, 64 usage.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/_worker.py" "$@"
