#!/usr/bin/env bash
# handoff.sh — M16.0 device-to-device handoff entry point.
#
# Thin bash wrapper. All logic lives in _handoff.py.
#
# Subcommands:
#   export   build a handoff bundle from this device
#   import   write a handoff bundle's contents into this device's state
#            (backs up existing state by default)
#   inspect  print a bundle's manifest without extracting
#
# Format: tar.gz with handoff/manifest.json + agent-continuity/ + claude/
# Defaults: include agent-continuity state; Claude Code transcripts opt-in
# Safety: import backs up existing state by default; refuses Claude
#         restoration on cross-user transfer (path-encoding mismatch).
#
# See docs/handoff.md for the full story.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_handoff.py" "$@"
