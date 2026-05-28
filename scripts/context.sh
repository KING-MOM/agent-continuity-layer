#!/usr/bin/env bash
# context.sh — project context snapshot entry point (M7.0).
#
# Continuity primitive: context recovery.
#
# Emits a single artifact that orients a fresh agent in under 60 seconds.
# Derived from git, CHARTER.md, docs/roadmap.md, the worker queue, and trust
# policy; the one operator-maintained field (next_safe_action) is sourced
# from core/context-pinned.json.
#
# Usage:
#   scripts/context.sh            # JSON to stdout (alias for --json)
#   scripts/context.sh --json     # JSON to stdout
#   scripts/context.sh --md       # markdown to stdout
#   scripts/context.sh --write    # regenerate core/context-snapshot.{json,md}
#
# Intentionally NOT a worker.sh subcommand. M7 is continuity, not worker
# execution; mixing them would invite worker-queue gravity to creep back
# in. See docs/handoff-vs-continuity.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/_context.py" "$@"
