# Mika / OpenClaw via Bridge

OpenClaw and Mika participate as one adapter among many. The `.mjs` bridge is the transport; the canonical state lives in this continuity layer, not in OpenClaw. If you're reading this and you came from OpenClaw expecting it to be the center of the system: it isn't. The bridge routes OpenClaw's queue/data plane through this layer for canonical state, audit, and trust — but the layer is the authority.

This framing isn't rhetorical — it's in [`../handoff-vs-continuity.md`](../handoff-vs-continuity.md). Read that if the framing surprises you.

## 1. What adapter am I?

An `openclaw-bridge` per [`core/schemas/adapter-identity.schema.json`](../../core/schemas/adapter-identity.schema.json):

```json
{
  "adapter_type": "openclaw-bridge",
  "adapter": "openclaw",
  "display_name": "OpenClaw / Mika bridge",
  "transport": ["bridge", "shell"],
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

The bridge can do everything a local-cli adapter can do (and it actually shells out to `scripts/worker.sh` to do it). The `bridge` transport tag distinguishes "I came through the .mjs runner" from "I'm calling the scripts directly."

## 2. What can I read?

Same canonical artifacts as everyone else. Through the bridge:

- Mika's MCP `worker_*` tools (e.g. `worker_show`, `worker_list`, `worker_dry_run_next`) read from `worker.sh` via the bridge.
- The bridge can also invoke `scripts/context.sh --json` and `scripts/decisions.sh list --json` directly.

## 3. What can I write?

The bridge's load-bearing path is `runTask`: claim → start → run subprocess → submit. Each transition shells out to `worker.sh`, which is the canonical writer.

When a worker submits a result through the bridge:

- The result is normalized by `agent-worker.mjs:normalizeWorkerResult`. **As of M8.3.2**, this normalizer preserves `decisions[]` — workers under the bridge can ship structured decisions in their results.
- The normalized JSON is handed to `worker.sh submit --result <tmpfile>`.
- `worker.sh submit` validates the `decisions[]` shape (M8.3.1: non-array `decisions` is rejected; the bridge passes through non-array values rather than coercing).
- Valid drafts go through `_decisions.append_entries_from_worker` and land in the canonical decision log with `refs: ["task:<task_id>", ...]`.

## 4. What command does the operator run?

The OpenClaw daemon runs the bridge under its existing process model. The operator-facing surface is OpenClaw's MCP tools, but the underlying invocation is:

```
~/.openclaw/workspace/scripts/agent-worker.mjs run-next
```

or, via OpenClaw's MCP layer, `worker_dry_run_next` followed by execution.

Direct shell access from the same host (e.g. for debugging) uses the standard `scripts/worker.sh` CLI; the bridge and direct shell paths converge on the same queue files.

## 5. What artifact comes back?

The same per-task JSON in `~/.cache/agent-continuity/queue/{state}/{task-id}.json`. The audit transitions visibly mark the bridge path:

```
audit.transitions:
  None    -> queued    by human
  queued  -> claimed   by codex-on-<host>-via-mjs       <- claimed_by string
  claimed -> running   by codex-on-<host>-via-mjs
  running -> completed by codex-on-<host>-via-mjs
claimed_by_adapter: codex                              <- the canonical enum brand
```

The `via-mjs` suffix in `claimed_by` is a bridge convention; the canonical adapter token is in `claimed_by_adapter`.

## 6. What trust boundary applies?

- Trust policy at `~/.config/agent-continuity/trust-policy.json` (the same file shell/bundle/MCP use). The bridge does not have its own policy.
- The bridge respects trust grants verbatim — `worker.sh claim` enforces them, and the bridge is just calling claim.
- OpenClaw's own daemon-level trust (which adapter is allowed to spawn subprocesses, etc.) is separate and lives outside this layer.

## 7. How do I verify with doctor?

```
scripts/doctor.sh
```

Two blocks to look at:

- `worker bridge` (existing M3/M4 check) reports the source adapter, installed extension, and OpenClaw CLI presence.
- `m9 adapter portability` reports `openclaw-bridge ok` when `~/.openclaw/workspace/scripts/agent-worker.mjs` is present. If missing, the M9 check reports `openclaw-bridge INFO` — the bridge is **optional**, not required for non-OpenClaw users.

If the bridge runs but submits silently drop `decisions[]`, the .mjs `normalizeWorkerResult` may have lost its M8.3.2 pass-through. Verify by submitting a worker-result whose `decisions[]` you can confirm later in the canonical log (e.g. with a distinctive `refs` value), then check `scripts/decisions.sh list --json | grep <ref>` — a missing entry points the finger at the normalizer. The M8.3.2 commit (git log under that tag) records the exact change; the M9.1 bundle artifacts at [`../artifacts/M9.1/`](../artifacts/M9.1/) demonstrate the bridge writeback working end-to-end through bundle transport, which exercises the same normalizer.

## See also

- [`../handoff-vs-continuity.md`](../handoff-vs-continuity.md) — why the worker queue and the bridge are subsystems, not the product
- [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) — adapter pattern spec
- [`codex-local-shell.md`](codex-local-shell.md) — what the bridge replaces if you have shell
- [`troubleshooting.md`](troubleshooting.md) — common failures
