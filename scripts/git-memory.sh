#!/usr/bin/env bash
# git-memory.sh — Git-backed durable memory repo helper.
#
# Scaffolds and exports curated continuity memory into an explicit private Git
# checkout. This intentionally does not symlink live runtime folders or ingest
# raw Claude/Codex/OpenClaw session stores.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/_git_memory.py" "$@"
