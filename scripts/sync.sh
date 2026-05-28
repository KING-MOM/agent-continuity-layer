#!/usr/bin/env bash
# sync.sh — multi-mode sync for the continuity layer.
#
# M3 mode (legacy, read-only pull of project context from VM):
#   sync.sh                          # pull all projects, JSON + human summary
#   sync.sh --list                   # list projects on VM, don't pull
#   sync.sh --project proj-foo-001   # pull one project only
#   sync.sh --trust-host             # bootstrap VM host-key pin
#   sync.sh --json | --human         # control output format
#
# M10.0 mode (bidirectional sync for global memory artifacts):
#   sync.sh push --artifact decisions
#   sync.sh pull --artifact decisions
#
#   M10.0 sync target: $AGENT_CONTINUITY_VM_PATH (fake-VM directory for
#   now; real SSH-pinned VM lands in M10.3).
#
# Charter invariant: M3 reads project context from VM into local cache.
# M10 syncs MEMORY ONLY — never scripts, schemas, skills, trust policy,
# worker queue, or OpenClaw bridge state. The VM is a memory store, not
# a code/config authority.
#
# Exit: 0 ok or no-op, 1 hard errors, 2 partial, 64 usage.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# M10.0: dispatch push/pull subcommands to _sync_artifact.py.
# Anything else (no subcommand or --flags) goes to the legacy M3 _sync.py
# so existing read-only-pull invocations stay byte-for-byte compatible.
case "${1:-}" in
  push|pull)
    exec python3 "$HERE/_sync_artifact.py" "$@"
    ;;
  *)
    exec python3 "$HERE/_sync.py" "$@"
    ;;
esac
