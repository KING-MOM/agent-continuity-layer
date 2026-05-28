# ChatGPT / Gemini / Grok / Kimi Web via Bundle

Web model chats join continuity through bundles: the operator exports one JSON object, pastes it into the chat, the agent returns one JSON object, and the operator ingests it. No filesystem, shell, API key, or browser extension is required on the model side.

Supported web adapter brands:

- `chatgpt`
- `gemini`
- `grok`
- `kimi`

`claude` web uses the same bundle transport and has its own walkthrough at [`claude-web-bundle.md`](claude-web-bundle.md).

## 1. What adapter am I?

A `web-agent` per [`core/schemas/adapter-identity.schema.json`](../../core/schemas/adapter-identity.schema.json). For ChatGPT:

```json
{
  "schema_version": "1.0",
  "adapter_id": "chatgpt-web-2026-05-27-operator",
  "adapter_type": "web-agent",
  "adapter": "chatgpt",
  "display_name": "ChatGPT web session",
  "transport": ["bundle"],
  "capabilities": {
    "whoami": true,
    "read_context": true,
    "read_decisions": true,
    "append_decision": "bundle-only",
    "claim_task": "bundle-only",
    "submit_result": "bundle-only"
  },
  "created_at": "2026-05-27T00:00:00Z"
}
```

For Gemini, Grok, or Kimi, change only `adapter_id`, `adapter`, and `display_name`:

| Model surface | `adapter` token | Example `adapter_id` |
|---|---|---|
| ChatGPT web | `chatgpt` | `chatgpt-web-operator` |
| Gemini web | `gemini` | `gemini-web-operator` |
| Grok web | `grok` | `grok-web-operator` |
| Kimi web | `kimi` | `kimi-web-operator` |

The token is descriptive, not cryptographic. Trust policy still decides whether writes succeed.

## 2. What can I read?

From the layer-to-adapter bundle the operator pastes into the chat:

- `bundle.context` — the M7 context snapshot.
- `bundle.decisions` — recent decision-log entries.
- `bundle.task` — at most one task included for work, review, or explanation.
- `bundle.bundle_claim` — task hash + id metadata that lets ingest reject stale returns.
- `bundle.allowed_operations` — what the operator expects the return bundle to attempt.

Read paths do not require trust grants; the operator chooses what to include in the export.

## 3. What can I write?

Through a return bundle, web adapters can write two things:

- `append_decisions[]` — durable decision entries attributed to `chatgpt`, `gemini`, `grok`, or `kimi`.
- `submit_results[]` — worker results for the exported task, including embedded `result.decisions[]` that flow through the M8.3 writeback path.

Writes are still operator-mediated and policy-gated:

- The operator must run `bundle.sh ingest` locally.
- Task submit requires the task target adapter to match the web adapter brand.
- Trust policy must allow that adapter for the repo/kind/trust level.
- The export-time task hash must still match the on-disk task at ingest time.

A web chat cannot silently claim work on its own. The bundle is the consent boundary.

## 4. What command does the operator run?

Export context + recent decisions only:

```bash
agent-continuity bundle export \
  --for-adapter chatgpt-web-operator \
  --decisions-limit 20 \
  --out /tmp/ac-bundle-export.json
```

Export a task for a web model to complete:

```bash
agent-continuity bundle export \
  --for-adapter chatgpt-web-operator \
  --task <task_id> \
  --decisions-limit 20 \
  --out /tmp/ac-bundle-export.json
```

Paste `/tmp/ac-bundle-export.json` into the web chat. The model returns an `adapter-to-layer` bundle. Save it and ingest:

```bash
agent-continuity bundle ingest /tmp/ac-bundle-return.json
```

Use `gemini-web-operator`, `grok-web-operator`, or `kimi-web-operator` for those surfaces. The `from_adapter.adapter` inside the return bundle must match the intended brand.

## 5. What artifact comes back?

The returned JSON bundle. After ingest, canonical state lives in the same places as every other adapter:

- decisions append to `$XDG_STATE_HOME/agent-continuity/decisions.jsonl`,
- task transitions append to the worker-task audit trail,
- worker-result decisions get `task:<task_id>` refs automatically.

The audit trail distinguishes transport with `claimed_by: bundle:<adapter_id>`.

## 6. What trust boundary applies?

Identity is descriptive; trust policy is enforcement. Adding a web model token makes attribution possible, not authority automatic.

Safe default remains conservative: fresh policies do not allow web model adapters. To let a web model complete tasks, the operator must add a repo-scoped policy/grant that includes the matching adapter in `allowed_workers`.

For pure decision append through `append_decisions[]`, ingest still validates the adapter identity, validates every draft, writes through the canonical decision-log writer, and rejects invalid batches all-or-nothing.

## 7. How do I verify with doctor?

```bash
agent-continuity doctor --human
```

Look for:

- `m9 adapter portability` -> `bundle ok`.
- `decisions log` -> entries appear after successful ingest.
- `worker queue` -> task status/audit reflects `bundle:<adapter_id>` after submit.

If ingest rejects, run it again with the same bundle after fixing the policy or task state issue. If the task hash changed, re-export a fresh bundle; stale bundles should not be forced through.

## See also

- [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) — adapter pattern spec
- [`claude-web-bundle.md`](claude-web-bundle.md) — same flow for Claude web
- [`codex-local-shell.md`](codex-local-shell.md) — local shell-capable worker path
- [`read-only-auditor.md`](read-only-auditor.md) — explicit no-write auditor stance
- [`troubleshooting.md`](troubleshooting.md) — common failures
