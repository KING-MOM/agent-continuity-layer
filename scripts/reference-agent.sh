#!/usr/bin/env bash
# reference-agent.sh — M13.3 minimal agent demo.
#
# Runs a deterministic local agent loop:
#   read_context -> decide -> append_decision
#
# This is a demo of adapter portability via the Python SDK, not an LLM runner.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/_reference_agent.py" "$@"
