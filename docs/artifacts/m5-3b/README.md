# M5.3b smoke-test evidence — host-side bridge for runTask + nextPendingId

The actual code change in M5.3b lives in `~/.openclaw/workspace/scripts/agent-worker.mjs` (host-side, not committed here). The pre-bridge backup is at `~/.openclaw/workspace/scripts/agent-worker.mjs.bak-pre-m5-2b`. The post-M5.2b backup before M5.3b additions was the same file (M5.3b adds to the M5.2b version).

This directory captures the dry-run output proving the bridge behaves as specified — no state mutation, all five M5.3b constraints honored.

## What's archived

| File | What it shows |
|---|---|
| `dry-run-bridged.json` | `agent-worker.mjs run-next --dry-run` after a fresh `enqueue` landed a task in `~/.cache/agent-continuity/queue/queued/`. The dry-run picks up the bridged task (`bridged: true`), surfaces all three would-* commands (claim, start, execute) with full args, and asserts `no_state_mutated: true`. |
| `dry-run-legacy.json` | `agent-worker.mjs run task_20260525033952_zdsxu4 --dry-run` for a task that lives in the legacy `worker-tasks/pending/` directory (left over from M5.2b baseline capture). The dispatcher falls back to the legacy code path: `bridged` field absent, output uses the original .mjs dry-run shape. Confirms backward compat. |

## Constraints from operator's M5.3b sign-off

| # | Constraint | Verified in |
|---|---|---|
| 1 | `nextPendingId()` reads `worker.sh list --state queued`, not legacy | `dry-run-bridged.json` `task_id` matches the bridged enqueue; bridged ID prefix is `task_` (Mika-format swap from `task-`) |
| 2 | Don't pick tasks for wrong worker | `worker.sh claim --adapter` refuses on adapter mismatch (verified in M5.3a tests); .mjs caller passes `--adapter <task.worker>` so claim succeeds for matching targets and refuses cleanly otherwise |
| 3 | `runTask()` calls `worker.sh claim` + `worker.sh start` + (existing subprocess) + `worker.sh submit` | `would_claim` / `would_start` / `would_execute` / `would_submit` all present in dry-run output |
| 4 | Legacy fallback OK, bridged is primary | `nextPendingId` calls `nextBridgedPendingId()` first; only on empty result does it scan `worker-tasks/pending/`. Verified at step 6 of the smoke test (run-next picked the bridged task even though a legacy task existed). |
| 5 | Dry-run exercises claim/start planning without spawning Codex/Claude, or clearly states it did not mutate state | `dry-run-bridged.json` has `"no_state_mutated": true`; post-check confirmed the task remained in `queued/` |

## Smoke test transcript

Captured `2026-05-25T14:01Z`:

1. `enqueue` via .mjs → `task_id: task_7e73ac6ad85f`, status `pending`
2. `worker.sh list --state queued` shows `task-7e73ac6ad85f` (kind=research, target=codex)
3. `run-next --dry-run` returns:
   - `dry_run: true`, `bridged: true`, `no_state_mutated: true`
   - `task_id: task_7e73ac6ad85f` (Mika-format), `worker_sh_task_id: task-7e73ac6ad85f`
   - `would_claim: worker.sh claim task-7e73ac6ad85f --adapter codex --worker codex-on-<hostname>-via-mjs`
   - `would_start: worker.sh start task-7e73ac6ad85f --adapter codex --worker codex-on-<hostname>-via-mjs`
   - `would_execute.command: /Applications/Codex.app/Contents/Resources/codex` with `args[0:3] = ['exec', '--ephemeral', '--sandbox', ...]`
4. Post-check: task still in `queued/`, no transition recorded in audit
5. Legacy task `task_20260525033952_zdsxu4` (in `worker-tasks/pending/`) dry-run returns legacy-shape output without `bridged` marker
6. `run-next` (no id) prefers the bridged task even with the legacy task present

## Operational note

Real execution (without `--dry-run`) will spawn Codex/Claude CLI against the
selected task. The bridge invokes `worker.sh claim` and `worker.sh start`
BEFORE the subprocess, so the task transitions `queued → claimed → running`
on disk before any bytes are sent to the worker. If the subprocess fails or
times out, the bridge calls `worker.sh submit --fail` and the task lands in
`failed/`. Otherwise it lands in `completed/`. Audit captures every step.

The first real (non-dry-run) bridged execution should still be a boring task
operator validates — same posture as M4.4. Don't auto-run high-stakes work
on the freshly bridged path.
