# ChatGPT Web via Bundle (read-only in M9.4)

ChatGPT (or any non-Anthropic web agent without filesystem or shell) joins continuity by reading an operator-exported JSON bundle. Bundles are the operator-mediated transport defined by [M9.1](../m9-adapter-pattern.md#m91---bundle-cli).

**Important**: as of M9.4, ChatGPT-web is **read-only via bundle**. The write path is not yet open because the underlying schemas and CLIs don't recognize an OpenAI/ChatGPT brand. See §3 for the specific constraints. Adding write support is M-future work.

## 1. What adapter am I?

A `web-agent` per [`core/schemas/adapter-identity.schema.json`](../../core/schemas/adapter-identity.schema.json):

```json
{
  "adapter_type": "web-agent",
  "adapter": "other",
  "display_name": "ChatGPT web session",
  "transport": ["bundle"],
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

`adapter: "other"` because the brand enum is `[claude, codex, openclaw, human, other]`; ChatGPT lives under `other` until or unless `openai` is added. The write capabilities are `false` because:

- `_bundle.py` ingest only allows `adapter` brand `claude` or `codex` (M9.1 v1 mirror of `worker.sh claim --adapter` enum).
- `core/schemas/decision-entry.schema.json` adapter enum is `[claude, codex, openclaw, human]` — `other` is not a valid write attribution.

Both constraints would need to relax before a ChatGPT-web write path could exist end-to-end.

## 2. What can I read?

From the layer-to-adapter bundle the operator pastes into the chat:

- `bundle.context` — the M7 context snapshot (current milestone, next safe action, recent activity)
- `bundle.decisions` — recent decision-log entries
- `bundle.task` — at most one task included for review (or null). You cannot *act on* a task in M9.4; you can read it and discuss.
- `bundle.allowed_operations` — `["read_context", "read_decisions"]` for a read-only bundle (no `--task` on export, or operator overrides explicitly).

## 3. What can I write?

Nothing through the contract in M9.4. If you attempt to return an adapter-to-layer bundle and the operator runs `bundle.sh ingest`, ingest rejects at the brand gate:

```
error: M9.1 bundle ingest supports adapter brands ['claude', 'codex'] (worker.sh
  claim --adapter enum constraint); got 'other'. Bundle was authored by an
  adapter brand outside M9.1's supported set.
```

To **discuss findings**, return them as conversation (not as a JSON envelope). To **record findings**, the operator (a `human` adapter on the local host) runs `scripts/decisions.sh add` themselves — see [`codex-local-shell.md`](codex-local-shell.md) §3 for the recording shape.

To **act on a task**, switch hosts: use [`claude-web-bundle.md`](claude-web-bundle.md) (Claude.ai) or [`codex-local-shell.md`](codex-local-shell.md) (local CLI).

## 4. What command does the operator run?

Export a read-only bundle:

```
scripts/bundle.sh export \
  --for-adapter chatgpt-web-2026-05-26-operator \
  --decisions-limit 20 \
  --out /tmp/bundle-export.json
```

Omitting `--task` produces a read-only bundle (allowed_operations: read_context + read_decisions only, no `bundle_claim`). The operator pastes the JSON into the chat. **No ingest step.** The bundle round-trip ends after the agent reads it.

## 5. What artifact comes back?

The exported bundle itself is the artifact. The chat conversation is where the agent's reading + reasoning lives.

If the agent's reasoning is load-bearing — e.g. they identified a missed decision — the operator records it via `decisions.sh add` on the host, attributing as `--adapter human --author operator` (or whatever the operator's identity is). The web agent's contribution is the conversation; the operator's contribution is the durable record.

## 6. What trust boundary applies?

- Read paths don't go through trust policy. The operator chooses what to include in the export; the agent reads it.
- The brand-gate rejection in §3 is itself a trust signal: the system refuses to attribute writes to an unrecognized adapter brand. That's deliberate — adding `openai` to the enums is an M-future decision that includes deciding what trust policy means for OpenAI-attributed writes.

## 7. How do I verify with doctor?

```
scripts/doctor.sh
```

- `m9 adapter portability` → `transports: ... bundle ok ...` confirms `bundle.sh` is reachable for export.
- `context snapshot` and `decisions log` blocks confirm the data being exported is current.

The doctor does NOT check whether the operator's chosen export `--for-adapter` will pass ingest; that constraint surfaces only at ingest time. M9.4 acknowledges this limitation in this walkthrough.

If something failed, see [troubleshooting.md](troubleshooting.md).

## See also

- [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) — adapter pattern spec
- [`claude-web-bundle.md`](claude-web-bundle.md) — write path open (`adapter: claude`)
- [`codex-local-shell.md`](codex-local-shell.md) — local write path (`adapter: codex`)
- [`read-only-auditor.md`](read-only-auditor.md) — explicit read-only stance
- [`troubleshooting.md`](troubleshooting.md) — common failures
