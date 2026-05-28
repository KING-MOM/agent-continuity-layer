#!/usr/bin/env bash
# migrate.sh — M12.3 schema migration runner entry point.
#
# Thin wrapper: discovers core/migrations/v{X}_to_v{Y}.py files and
# walks the user from their current version to the target version,
# one edge at a time. All real logic lives in _migrate.py (same
# pattern as _doctor.py, _decisions.py, _context.py).
#
# Usage:
#   migrate.sh --dry-run                  plan only, never writes
#   migrate.sh                            apply (refuses without valid plan)
#   migrate.sh --from V --to V            override versions (testing/repair)
#
# Invariants (do NOT break without operator sign-off):
#   - --dry-run never writes.
#   - apply refuses to run without a valid plan from the same logic.
#   - never invoked automatically by install.sh, doctor, or any
#     other script. The operator runs this explicitly.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_migrate.py" "$@"
