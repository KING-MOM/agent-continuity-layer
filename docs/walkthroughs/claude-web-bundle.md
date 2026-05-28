# Claude Web via Bundle

Same transport as [ChatGPT web](chatgpt-web-bundle.md) — a JSON envelope the operator hands to the agent and receives back. The only meaningful difference is the adapter brand. Read this if you're using claude.ai or another Anthropic web surface without filesystem or shell access.

## 1. What adapter am I?

A `web-agent` per [`core/schemas/adapter-identity.schema.json`](../../core/schemas/adapter-identity.schema.json):

```json
{
  "adapter_type": "web-agent",
  "adapter": "claude",
  "display_name": "Claude web session",
  "transport": ["bundle"],
  "capabilities": {
    "whoami": true,
    "read_context": true,
    "read_decisions": true,
    "append_decision": "bundle-only",
    "claim_task": "bundle-only",
    "submit_result": "bundle-only"
  }
}
```

`adapter: "claude"` (the brand exists in the schema enum). Decisions you append are attributed `adapter: claude` in the canonical log.

## 2. What can I read?

Same as the ChatGPT walkthrough: `bundle.context`, `bundle.decisions`, `bundle.task`, `bundle.bundle_claim`, `bundle.allowed_operations`. See [chatgpt-web-bundle.md §2](chatgpt-web-bundle.md#2-what-can-i-read) for the field-by-field breakdown.

## 3. What can I write?

`append_decisions[]` and (if a task was exported to you) `submit_results[]`. The auto-injected ref is `bundle:<bundle_id>`. Decisions you embed inside `submit_results[*].result.decisions[]` flow through the M8.3 writeback path and get a `task:<task_id>` ref instead.

## 4. What command does the operator run?

```
scripts/bundle.sh export \
  --for-adapter claude-web-2026-05-26-operator \
  --task <task_id> \
  --decisions-limit 20 \
  --out /tmp/bundle-export.json
```

The operator pastes the JSON into the Claude chat. Claude reads it, produces a return bundle, and pastes that back. The operator runs:

```
scripts/bundle.sh ingest /tmp/bundle-return.json
```

## 5. What artifact comes back?

Same ingest report shape as ChatGPT web; the only differences are:

- `from_adapter.adapter` is `"claude"`.
- Decisions land in the canonical log with `adapter: "claude"`.
- `task.claimed_by_adapter` becomes `"claude"`; `claimed_by` becomes `"bundle:claude-web-..."`.

This is what the [M9.1 evidence](../artifacts/M9.1/) actually demonstrates: a `task-m91-smoke-*.json` with `claimed_by_adapter: claude` and an audit chain stamped `bundle:claude-web-m91`.

## 6. What trust boundary applies?

Identical to ChatGPT web. Identity is descriptive; bundle ingest enforces envelope + identity + `bundle_claim.task_hash` + trust policy. The hash check catches any task mutation, not just narrow races.

The one wrinkle: if the operator wants Claude-attributed decisions to compose with M8 decisions written by a local Claude CLI (which would also use `adapter: claude`), both writers produce mergeable entries. The provenance ref distinguishes — `bundle:<id>` vs `task:<id>` — but the `adapter` field is the same.

## 7. How do I verify with doctor?

```
scripts/doctor.sh
```

Same blocks as ChatGPT web. To confirm the bundle path is the canonical claude-web entry point on this host:

```
scripts/doctor.sh --json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['checks']['m9_adapter_portability']['transport_summary'])"
```

You should see `"bundle"` in the list.

## See also

- [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) — adapter pattern spec
- [`chatgpt-web-bundle.md`](chatgpt-web-bundle.md) — same flow, `adapter: other`
- [`read-only-auditor.md`](read-only-auditor.md) — bundle without writes
- [`troubleshooting.md`](troubleshooting.md) — common failures
