# Codex Local via Shell

You're running Codex (or any local LLM CLI) on a host that has shell access to this repo. You can invoke the canonical scripts directly — no bundle round-trip, no MCP server, no operator hand-off. This is the simplest path.

## 1. What adapter am I?

A `local-cli` per [`core/schemas/adapter-identity.schema.json`](../../core/schemas/adapter-identity.schema.json):

```json
{
  "adapter_type": "local-cli",
  "adapter": "codex",
  "display_name": "Codex local on <host>",
  "transport": ["shell"],
  "capabilities": {
    "whoami": true,
    "read_context": true,
    "read_decisions": true,
    "append_decision": true,
    "claim_task": true,
    "submit_result": true
  }
}
```

All six operations are `true` (not `bundle-only`) — you can hit the contract directly.

## 2. What can I read?

Three shell commands:

```
scripts/context.sh --json     # context-snapshot.json
scripts/decisions.sh list --json
scripts/worker.sh --json list --state queued
```

Each returns the same canonical artifact the bundle/MCP transports return — see [M9.2 compat evidence](../artifacts/M9.2/compat-summary.md) for the equivalence proof.

## 3. What can I write?

```
scripts/decisions.sh add \
  --adapter codex \
  --decision "..." \
  --why "..." \
  --ref "task:task-..." --ref "commit:abc1234"
```

```
scripts/worker.sh claim <task_id> --adapter codex --worker codex-on-<host>
scripts/worker.sh start <task_id> --adapter codex --worker codex-on-<host>
scripts/worker.sh submit <task_id> --worker codex-on-<host> --result /tmp/result.json
```

A worker-result with embedded `decisions[]` triggers M8.3 writeback automatically on submit — same path the bundle and MCP transports use.

## 4. What command does the operator run?

Nothing on your behalf. You are the operator (or you act with the operator's authority on the same host). Worker id convention: `codex-on-<host>` for direct shell, parallel to `bundle:<adapter_id>` and `mcp:<adapter_id>` for the other transports.

If trust policy refuses the claim, you'll see a structured rejection from `worker.sh`. Run:

```
scripts/worker.sh trust-check <task_id>
```

to see why before the claim attempt.

## 5. What artifact comes back?

- `decisions.sh add` prints the new decision's sha256 id on stdout (pipeable). A human summary goes to stderr.
- `worker.sh claim` / `start` / `submit` print JSON reports (with `--json`) carrying the next state and any audit transitions.
- The decision log gains a row with `adapter: codex`, `refs: ["task:<task_id>", ...]` (when written via worker submit) or your chosen refs (when written via `decisions.sh add`).

## 6. What trust boundary applies?

- Trust policy at `~/.config/agent-continuity/trust-policy.json` gates `worker.sh claim` and `worker.sh submit`. Per-repo grants matter; default is deny.
- Decision writes don't go through trust policy in M8 — anyone who can run `decisions.sh add` can append. The provenance (adapter, author, task ref) is the audit signal.
- You can run `worker.sh trust-check <task_id>` to see whether a queued task would be claimable under the current policy.

## 7. How do I verify with doctor?

```
scripts/doctor.sh
```

Look for:

- `m9 adapter portability` block → `transports: shell ok`. If shell is anything other than ok, your repo's `scripts/context.sh`, `scripts/decisions.sh`, or `scripts/worker.sh` is missing — the underlying foundation is broken.
- `trust policy` block → `present, grants count: N`. If absent, every claim will fail policy resolution; add at least a default grant or per-repo policy.

## See also

- [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) — adapter pattern spec
- [`../artifacts/M9.2/compat-summary.md`](../artifacts/M9.2/compat-summary.md) — proof that shell / MCP / bundle produce the same artifacts
- [`mika-openclaw-bridge.md`](mika-openclaw-bridge.md) — what the bridge does that direct shell doesn't
- [`troubleshooting.md`](troubleshooting.md) — common failures
