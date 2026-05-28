#!/usr/bin/env bash
# install-thin-skills.sh — install agent-continuity SKILL.md into each agent home.
# Implementation lives in _install_thin_skills.py; this is a thin wrapper.
#
# Usage:
#   install-thin-skills.sh                       # dry-run, JSON + human summary
#   install-thin-skills.sh --apply               # actually write
#   install-thin-skills.sh --apply --agent claude   # only one agent
#   install-thin-skills.sh --apply --force       # required to downgrade
#
# Default = dry-run. Never overwrites without backup.
# Exit: 0 nothing-to-do, 1 errors, 2 actions pending (--apply or --force), 64 usage.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/_install_thin_skills.py" "$@"
