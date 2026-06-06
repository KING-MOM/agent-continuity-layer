#!/usr/bin/env bash
# watch.sh — M9.4 opt-in agent-home watcher entry point.
#
# Thin bash wrapper. All logic lives in _watch.py.
#
# Subcommands:
#   enable               install LaunchAgent + initial sync (macOS only)
#   disable              launchctl unload + remove LaunchAgent
#   status [--json]      show enabled/disabled, last tick, audit summary
#   --tick               invoked by launchd; detect drift + apply
#
# Default off. Substrate identity remains "tool", not "background service",
# unless the operator explicitly enables this. See docs/watch.md for full
# trust posture, TCC permission notes, and rollback procedure.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_watch.py" "$@"
