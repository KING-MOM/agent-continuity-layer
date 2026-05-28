# adapters/claude

**Role:** Worker.

This adapter teaches Claude Code how to:
1. Read project context from the continuity layer (read-only).
2. Claim a worker task from OpenClaw's queue.
3. Execute bounded work within the task's `trust_level` and `files_allowed`.
4. Write artifacts back: a patch, a report, a decision entry.
5. Refuse anything that exceeds its trust level — escalate to OpenClaw for human approval.

## What Claude is good for (in this system)

- Reasoning over context (`context.md`, `decisions.md`).
- Code review.
- Generating artifacts (briefs, summaries, reports).
- Multi-file refactors that need understanding before mechanical changes.

## What Claude is NOT for (route to Codex instead)

- Long shell-driven repo execution.
- Test/build loops.
- Bulk mechanical edits.

## Interfaces

| Operation | Direction | Surface |
|---|---|---|
| Read context | VM → here | reads `context.md`, `decisions.md` (read-only) |
| Claim task | queue → here | transitions task `queued` → `claimed` |
| Append decision | here → VM | one line, per `core/decision-log.md` format |
| Submit result | here → OpenClaw | result block per `worker-task.schema.json` |

## Status

Stub. The actual Claude integration is a thin skill ([`../../skills/claude/SKILL.md`](../../skills/claude/SKILL.md)) that points Claude here.
