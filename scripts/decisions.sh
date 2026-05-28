#!/usr/bin/env bash
# decisions.sh — cross-agent decision log entry point (M8.0).
#
# Continuity primitive: decision log.
#
# Append-only writeback of WHY non-obvious choices were made so a future
# agent — different model, different session, different device — can rely
# on the reasoning without re-asking the operator.
#
# Storage: $XDG_STATE_HOME/agent-continuity/decisions.jsonl
#          (defaults to ~/.local/state/agent-continuity/decisions.jsonl)
#
# Subcommands:
#   add   append a new decision entry (schema-validated, lock-protected)
#   list  list entries (newest first); --json for raw JSONL
#
# Intentionally NOT a worker.sh subcommand. M8 is decision log, not
# delegated task execution. Mixing them would invite the same worker-
# queue gravity M7 avoided — see docs/handoff-vs-continuity.md.
#
# No edit/delete: the only way to mutate is to append a new entry.
# `rm` outside this CLI is the destructive escape hatch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/_decisions.py" "$@"
