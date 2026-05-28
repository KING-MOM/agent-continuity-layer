#!/usr/bin/env bash
# bundle.sh — adapter bundle export/ingest CLI (M9.1).
#
# Continuity primitive: adapter portability.
#
# Operator-mediated transport for adapters that cannot reach the
# continuity layer directly (web agents in chat surfaces, restricted
# environments). Two subcommands:
#
#   export    package context + decisions + optional task into a single
#             JSON object the operator hands to an agent
#   ingest    apply an agent's return bundle: validates envelope and
#             identity, routes append_decisions through the canonical
#             decision log writer, routes submit_results through worker.sh
#             claim/start/submit on the operator's behalf
#
# Bundle ingest is the operator-mediated claim boundary: a web agent
# cannot hold a real local claim lifecycle, so the bundle envelope IS
# the claim, audited as `by: bundle:<adapter_id>` in worker-task records.
#
# Source logs remain append-only — ingest never edits decisions.jsonl
# or queue files directly; it routes through canonical writers
# (_decisions.append_entries_from_bundle, worker.sh claim/start/submit).
#
# See docs/m9-adapter-pattern.md for the canonical spec.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/_bundle.py" "$@"
