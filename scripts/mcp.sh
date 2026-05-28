#!/usr/bin/env bash
# mcp.sh — M9.2 MCP tool surface entry point.
#
# Continuity primitive: adapter portability.
#
# Exposes the six adapter-contract operations (whoami, read_context,
# read_decisions, append_decision, claim_task, submit_result) as a CLI
# dispatcher. Each tool is a thin wrapper over an existing script
# (context.sh, decisions.sh, worker.sh) — MCP is a transport, not new
# orchestration.
#
# Subcommands:
#   list-tools     print the tool manifest (core/mcp/tools.json)
#   tool <name>    invoke a tool with --args '<json>'
#
# A full MCP server (JSON-RPC over stdio or HTTP) is deferred to a later
# slice. This CLI is the operational substance; wrapping it in a server
# transport is mechanical.
#
# See docs/m9-adapter-pattern.md for the contract semantics.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/_mcp.py" "$@"
