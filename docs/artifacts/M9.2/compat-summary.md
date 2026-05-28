# M9.2 Compatibility Evidence

Proves the M9.0 six-operation contract translates to MCP without inventing
a second semantics layer. Each MCP tool handler in `scripts/_mcp.py` is a
thin wrapper over an existing shell entry point (`context.sh`,
`decisions.sh`, `worker.sh`); the artifacts here are the shell-vs-MCP
output for each operation, captured during the M9.2 ship.

## Read operations (logical-equivalence verified)

| Operation | Shell input | MCP input | Result |
|---|---|---|---|
| `read_context` | `scripts/context.sh --json` | `mcp.sh tool read_context` | Logical-equivalent (only `generated_at` differs by call wall-clock; all other fields byte-identical). See `read-context-shell.json` vs `read-context-mcp.json`. |
| `read_decisions` (unfiltered) | `scripts/decisions.sh list --json` | `mcp.sh tool read_decisions` | Byte-identical decision sequence; same ids, same order. See `read-decisions-shell.jsonl` vs `read-decisions-mcp.json`. (Shell emits JSONL, MCP emits a JSON array — same content, different wire format.) |
| `read_decisions` (`--adapter claude`) | `scripts/decisions.sh list --json --adapter claude` | `mcp.sh tool read_decisions --args '{"adapter":"claude"}'` | Identical filter behavior. See `read-decisions-filter-shell.jsonl` vs `read-decisions-filter-mcp.json`. |

## Write operations (semantic equivalence verified)

| Operation | Shell | MCP | Result |
|---|---|---|---|
| `append_decision` (identical input) | `scripts/decisions.sh add ...` | `mcp.sh tool append_decision --args '...'` | Both produce schema-valid entries. With identical input bodies submitted in the same second, the sha256 ids collide (M8.0's deterministic content-addressing property) — the MCP wrapper does not introduce divergent provenance. |

## Fixture-based write operations (no real Codex/Claude)

| Operation | Fixture | MCP result |
|---|---|---|
| `claim_task` | hand-crafted queued task with `kind: research` | Task moved to `claimed/` with `claimed_by: mcp:mcp-fixture-test`, `claimed_by_adapter: claude`. See `claim-task-fixture-result.json`. |
| `submit_result` | worker-result with embedded `decisions[]` | Task moved to `completed/`; the embedded decision flowed through M8.3 writeback and `result.appended_decision_ids` is populated. See `submit-result-fixture.json` and `task-m92-fixture-*.json`. |

## Audit trail through MCP (preserved)

The fixture task's audit:

```
None    -> queued    by human
queued  -> claimed   by mcp:mcp-fixture-test
claimed -> completed by mcp:mcp-fixture-test
```

The `mcp:<adapter_id>` worker-id convention parallels M9.1's
`bundle:<adapter_id>`, so any future reader of the worker-task log can
visibly distinguish operator-mediated paths (bundle, MCP) from direct
adapter writes.

## What this evidence does NOT prove

- No full MCP wire-protocol server (stdio JSON-RPC, HTTP) was implemented;
  M9.2 ships the manifest + handlers + CLI dispatcher (`mcp.sh tool <name>`).
  Wrapping in a server transport is deferred until a real consumer
  surfaces the need.
- No real Codex or Claude was invoked. The `claim_task`/`submit_result`
  fixture uses a hand-crafted task and a synthetic worker-result. Real
  worker execution remains the existing M4/M5 / M8.3 path; MCP is the
  transport.
- No multi-client concurrency tests; the canonical concurrency
  guarantees (queue claim race-safety via `os.rename`, decision log lock)
  come from the underlying scripts the MCP handlers wrap.

## Artifacts in this directory

```
read-context-shell.json            shell read_context output
read-context-mcp.json              MCP read_context output
read-decisions-shell.jsonl         shell read_decisions output (jsonl)
read-decisions-mcp.json            MCP read_decisions output (json array)
read-decisions-filter-shell.jsonl  shell read_decisions --adapter=claude
read-decisions-filter-mcp.json     MCP read_decisions adapter=claude
claim-task-fixture-result.json     MCP claim_task return value
submit-result-fixture.json         MCP submit_result return value
task-m92-fixture-<ts>.json         final completed task with audit
compat-summary.md                  this file
```
