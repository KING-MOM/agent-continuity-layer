# M5.2b archive — agent-worker.mjs historical tasks

Verbatim copies of pre-bridge `worker-tasks/done/` and `worker-tasks/failed/`
entries from `~/.openclaw/workspace/worker-tasks/`. Captured 2026-05-25 as
part of M5.2b, before the bridge to `agent-continuity-layer/scripts/worker.sh`
took the data plane over.

Originals were NOT deleted from the source — this is a snapshot, not a move.
Operator can remove the source whenever they're satisfied the bridge holds:

    rm -rf ~/.openclaw/workspace/worker-tasks/done/*.json
    rm -rf ~/.openclaw/workspace/worker-tasks/failed/*.json

Per the operator-side memory `feedback_no_unilateral_queue_cleanup`, this
archive exists so a future operator (or future me) can audit `.mjs`'s
historical behavior without depending on the host filesystem.

## Layout

- `done/{task_<ts>_<hex>}.json` — successfully completed .mjs tasks
- `failed/{task_<ts>_<hex>}.json` — failed .mjs tasks (timeouts, schema
  validation failures, worker crashes)

## Format

Each file is the .mjs task shape, NOT the continuity layer's shape:

    {
      task_id, worker, mode, repo, goal, permissions, expected_output,
      status, created_at, updated_at, timeout_sec, started_at,
      finished_at, result, process
    }

Bridge round-trip preserves these shapes via the M5.2b sidecar at
`~/.openclaw/workspace/worker-tasks/_mika_compat/{task_id}.json`.
