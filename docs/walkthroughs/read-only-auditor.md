# Read-Only Auditor

You want to look without touching. Use cases: a human inspecting state before a meeting; a CI job verifying continuity invariants; a monitoring tool watching the decision log; a fresh agent orienting before deciding whether to do anything. None of these need write capabilities, and giving them write capabilities is unnecessary risk.

## 1. What adapter am I?

A `read-only-auditor` per [`core/schemas/adapter-identity.schema.json`](../../core/schemas/adapter-identity.schema.json):

```json
{
  "adapter_type": "read-only-auditor",
  "adapter": "human",
  "display_name": "Audit / inspection session",
  "transport": ["shell", "bundle"],
  "capabilities": {
    "whoami": true,
    "read_context": true,
    "read_decisions": true,
    "append_decision": false,
    "claim_task": false,
    "submit_result": false
  }
}
```

`capabilities.append_decision`, `.claim_task`, `.submit_result` are all `false` — the auditor declares it cannot write, and the matrix surfaces that to a host UI. Trust policy still enforces; the declaration is a UX hint, not a security boundary.

## 2. What can I read?

Everything the contract exposes:

- `scripts/context.sh --json` — current snapshot (M7)
- `scripts/context.sh --md` — same content, markdown render
- `scripts/decisions.sh list --json` — newest-first decision log, filterable by `--repo` / `--adapter` / `--limit`
- `scripts/worker.sh --json list` — queue state by state
- `scripts/worker.sh --json show <task-id>` — full task body + audit transitions

If you're using the bundle transport (e.g. you live in a web agent surface), the operator can export a **read-only bundle** for you to consume:

```
scripts/bundle.sh export --for-adapter audit-2026-05-26-operator --decisions-limit 100 --out /tmp/audit.json
```

A bundle without `--task` gets `allowed_operations: ["read_context", "read_decisions"]` automatically — write operations aren't surfaced because there's nothing to write against. The auditor reads this bundle in-chat. **No ingest step happens.** The bundle round-trip ends after reading.

## 3. What can I write?

Nothing through the contract. The auditor declaration says `false` for every write capability. If you find yourself wanting to write, you're using the wrong adapter type.

If your audit finds drift and wants to record a decision about it, the path is **operator-driven, not auditor-driven**: the auditor reports findings as conversation; the operator (a human adapter on the local host) runs `scripts/decisions.sh add` to record. See [`codex-local-shell.md`](codex-local-shell.md) §3 for the recording shape.

This separation is deliberate — bundle ingest rejects `adapter: human` at its brand gate (M9.1 v1 allows only `claude`/`codex` for ingest), so even a "notes-only" return bundle won't ingest. The auditor's voice flows back as conversation, not envelope.

## 4. What command does the operator run?

For a quick CI-style audit on the host:

```
scripts/doctor.sh --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
checks = d['checks']
for name, block in checks.items():
    print(f'{name}: {block[\"status\"]}')
"
```

For a web-based audit (operator hands a bundle to a web agent):

```
scripts/bundle.sh export --for-adapter audit-... --decisions-limit 100 --out /tmp/audit.json
```

The auditor reviews the bundle in-chat. **The operator does not run `bundle.sh ingest`** for a read-only audit — there is nothing to ingest. The audit conversation IS the deliverable.

## 5. What artifact comes back?

For shell audits: the JSON / markdown the read commands print. Nothing persists unless the operator chooses to log it via `decisions.sh add`.

For bundle audits: the exported bundle itself is the artifact for the agent; the operator's record of the audit (if any) is what `decisions.sh add` writes back on the host, attributed `--adapter human --author <operator>`. The auditor's findings flow through the operator's hands, not through ingest.

## 6. What trust boundary applies?

- Read paths don't go through trust policy in M9; trust gates writes. The auditor's declared `capabilities` are informational — even if the auditor lied and said `append_decision: true`, the policy would still gate the write attempt.
- The auditor SHOULD use a distinct `adapter_id` (e.g. `audit-ci-2026-05-26`) so logs and any future decision-attribution can distinguish "audit" from "operator action."

## 7. How do I verify with doctor?

The auditor's main job *is* the doctor, in a way. The relevant blocks:

- `context snapshot` — fresh? stale? matches HEAD?
- `decisions log` — count, newest_ts, adapters, repos
- `worker queue` — depth per state
- `m9 adapter portability` — what transports work; which are broken

For automated runs:

```
scripts/doctor.sh --json
```

Returns the full report. Exit code is `0` on all-OK, `2` on any WARN, `1` on any ERROR — usable in CI.

## See also

- [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) — adapter pattern spec
- [`chatgpt-web-bundle.md`](chatgpt-web-bundle.md) — for the auditor-via-web flow
- [`codex-local-shell.md`](codex-local-shell.md) — escalating from auditor to writer
- [`troubleshooting.md`](troubleshooting.md) — common failures
