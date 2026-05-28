#!/usr/bin/env bash
# project.sh — M14.0 local project registry entry point.
#
# Thin wrapper. All logic lives in _project.py (same pattern as
# _doctor.py, _decisions.py, _context.py, _migrate.py).
#
# Subcommands:
#   list                              JSON or human listing
#   add [--path P] [--name N] ...     register a project (idempotent)
#   info <uuid-or-name-substring>     show one entry
#   remove <uuid-or-name-substring>   delete one entry (needs --yes)
#
# Storage: $XDG_CONFIG_HOME/agent-continuity/projects/<uuid>.json
# Schema:  core/schemas/project-registry-entry.schema.json
#
# Auto-registration on first write-side operation is handled by
# scripts that call ensure_project_registered() from _project.py.
# This wrapper is for explicit CLI use only.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_project.py" "$@"
