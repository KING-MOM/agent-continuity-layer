#!/usr/bin/env bash
# transcript.sh — M17.0 local Claude Code transcript index entry point.
#
# Thin bash wrapper. All logic lives in _transcript.py.
#
# Subcommands:
#   list                                list local sessions with metadata
#   show <session-id-or-prefix>         show one session in detail
#   path <session-id-or-prefix>         emit the JSONL absolute path
#
# Reads ~/.claude/projects/<encoded>/<uuid>.jsonl files. Pure indexing —
# does not modify transcripts, does not sync, does not write substrate
# decisions. M17.1 (heuristic compile to decisions) and M17.2 (LLM-based
# session summary) build on top of this read-only inventory.
#
# See docs/transcript.md for the full feature documentation.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_transcript.py" "$@"
