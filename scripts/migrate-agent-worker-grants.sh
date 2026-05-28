#!/usr/bin/env bash
# migrate-agent-worker-grants.sh — migrate trust grants from agent-worker.mjs's
# policy file to the continuity layer's host-side policy. M5.2b deliverable.
#
# Usage:
#   migrate-agent-worker-grants.sh               # dry-run, JSON + human summary
#   migrate-agent-worker-grants.sh --apply       # actually write (backs up first)
#   migrate-agent-worker-grants.sh --json        # JSON only
#
# Source: ~/.openclaw/workspace/worker-tasks/trust-policy.json
# Target: ~/.config/agent-continuity/trust-policy.json
#
# Grants are appended (de-duped by grant_id). The target is backed up to a
# .bak-<timestamp> file before any change.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/_migrate_agent_worker_grants.py" "$@"
