# Context format

Every project on the VM lives under `~/life-agents/sessions/{project_uuid}/` and contains exactly three files:

| File | Purpose | Owner | Cadence |
|---|---|---|---|
| `context.md` | Dense, current snapshot of project state | Any adapter | Updated on every meaningful interaction; overwritten in place |
| `decisions.md` | Append-only log of design / scope / tool decisions | Any adapter | Append after a decision is made |
| `history.md` | Compressed chat history (sliding window) | OpenClaw only | Rewritten by compressor |

Carried forward verbatim from v0.1. See [`v0.1-reference/life-agents-unified/SKILL.md`](../v0.1-reference/life-agents-unified/SKILL.md) §"Context Compression Strategy" for the cadence.

## What v0.2 changes

- These files are now **read by adapters**, not directly by the skill bundle. The adapter is responsible for the SSH/rsync transport.
- Adapters must respect read-only for non-owner adapters. Only the OpenClaw adapter writes `history.md`. Claude/Codex adapters may append to `decisions.md` (with a worker-task ID for attribution) and rewrite `context.md`.
- Every write must include a trailing comment line identifying the source adapter + worker-task ID (if any):
  ```
  <!-- written-by: claude adapter / task-abc123def456 / 2026-05-23T18:00:00Z -->
  ```

## Decision-entry format

Decisions are line-oriented. One entry per line. See [`decision-log.md`](decision-log.md).
