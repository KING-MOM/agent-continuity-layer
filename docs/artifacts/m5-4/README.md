# M5.4 — Mika MCP tool verification through the bridge

Verify-only milestone: invoke each tool registered by
`~/.openclaw/workspace/.openclaw/extensions/agent-worker/index.js` against
the bridged `.mjs`, capture the response shape Mika consumes, surface any
awkwardness.

Per operator's directive: don't build the new direct extension at
`.openclaw/extensions/agent-continuity/` unless real verification shows
awkwardness. It did, but the fix was a 2-line patch to the existing
extension — much smaller than building a parallel extension.

## What's archived here

| File | What it shows |
|---|---|
| `smoke-harness.mjs` | Reproducible Node.js script that imports the extension, mocks `api.registerTool` to capture tool defs, then invokes each tool's `execute()` with realistic params |
| `worker_enqueue.json` | **PRE-FIX** output. `details.ok: false`, `details.error: "Worker enqueue did not return a task JSON."`. The task was created in the queue regardless. |
| `worker_enqueue_POSTFIX.json` | **POST-FIX** output. `details.ok: true`, `details.task.task_id` populated, `details.policy.queued_only: true`. |
| `worker_list.json` | `details.ok: true`, `details.queue_root`, `details.raw` contains the JSON of all tasks (bridged + legacy), `details.tasks: []` (pre-existing — parseListOutput regex doesn't match JSON, Mika reads raw) |
| `worker_list_pending.json` | Same shape, filtered to `pending`. `raw` correctly contains the bridged task |
| `worker_show_new.json` | Full Mika-shape task (12 fields): task_id, worker, mode, repo, goal, permissions, expected_output, state, status, created_at, updated_at, timeout_sec. `state: 'pending'` correctly mapped from worker.sh's `queued` |
| `worker_show_completed.json` | `worker_show` against the M5.3 task (`task_e49c5caffbea`). `state: 'done'`, `task.status: 'done'` — bridge round-trip on a completed task verified |
| `worker_show_POSTFIX.json` | Post-fix roundtrip: enqueue → show with the same task_id returns matching task |
| `worker_dry_run_next.json` | `details.dry_run: true`, `details.policy.no_execution: true`, `details.output` contains the bridged dry-run JSON with `bridged: true` + `no_state_mutated: true` |
| `worker_trust_list.json` | `details.policy: {version: 1, grants: 0}` — correct .mjs format. (Doctor's `grants_count: 1` reflects `repos[]` entries, not grants[] — different field.) |
| `worker_trust_check.json` | Full result shape: `task_id`, `state`, `trusted: false`, `grant_id: null`, `policy_path`, `checked: {worker, mode, repo, filesystem, can_run_tests, network, timeout_sec}` |

## The fix

File: `~/.openclaw/workspace/.openclaw/extensions/agent-worker/index.js` (host-side).
Backup at `index.js.bak-pre-m5-4`.

### Before

```javascript
const text = runWorker(args);
const task = safeParseJsonText(text);
if (!task?.task_id) return { ok: false, error: 'Worker enqueue did not return a task JSON.', raw: text };
return { ok: true, task, policy: {...} };
```

### After

```javascript
const text = runWorker(args);
const parsed = safeParseJsonText(text);
// M5.4 fix: .mjs enqueue always wraps as {queued: true, task: {task_id, ...}}.
// The original wrapper read parsed.task_id directly, which was always undefined
// (the task lives one level deeper). Tolerate both shapes via the fallback so
// a future unwrapped .mjs response would also work.
const task = parsed?.task ?? parsed;
if (!task?.task_id) return { ok: false, error: 'Worker enqueue did not return a task JSON.', raw: text };
return { ok: true, task, policy: {...} };
```

The fix is intentionally tolerant of both shapes — if a future `.mjs` version
ever returns the task at the top level (no `queued`/`task` wrapper), the
fallback `?? parsed` handles it without another change.

## Status of the 6 Mika MCP tools after M5.4

| Tool | Pre-fix | Post-fix |
|---|---|---|
| `worker_enqueue` | ⚠ ok: false (wrapper bug; task created regardless) | ✓ ok: true, full task body |
| `worker_list` | ✓ works (raw has data; tasks[] empty by pre-existing parseListOutput limitation) | unchanged |
| `worker_show` | ✓ works (full 12-field Mika shape) | unchanged |
| `worker_dry_run_next` | ✓ works (bridged dry-run, no_state_mutated) | unchanged |
| `worker_trust_list` | ✓ works | unchanged |
| `worker_trust_check` | ✓ works (full checked block) | unchanged |

## Charter framing

This milestone strengthens **adapter portability**: Mika's MCP surface is
the canonical adapter between Mika sessions and the continuity layer's
worker queue. After M5.4, all 6 owner-only tools work end-to-end through
the bridged `.mjs`, with shapes preserved from Mika's POV.

## Out of scope

- Building a new extension at `.openclaw/extensions/agent-continuity/` —
  not needed since the existing extension now works.
- Fixing the `parseListOutput` regex (it never matched .mjs's JSON output;
  Mika reads `raw` instead). Pre-existing condition.
- Replace milestone (M5.5): keep `.mjs` as execution engine vs port to
  Python. Decision deferred.

## Pre-test queue state

Two tasks remain as evidence:
- `task-e49c5caffbea` (completed, M5.3 real-run validation)
- `task-e3414b19657d` (rejected, M5.2b smoke artifact — policy enforcement evidence)

My own M5.4 test artifacts (3 stale queued tasks) were cleaned up after
evidence capture; per `feedback_no_unilateral_queue_cleanup`, scope-limited
cleanup of my own test artifacts is fine post-evidence-archive.
