---
name: agent-continuity
version: "0.1.0"
role: claude
description: "Claude Code-side worker for the agent-continuity-layer. Reads project context (read-only), claims worker tasks dispatched by OpenClaw, executes within the task's trust_level + files_allowed, and submits artifacts back. Triggers when the user mentions: 'continue from another device', 'pick up where I left off', 'load project context', or when OpenClaw dispatches a task targeting Claude."
---

# Agent Continuity Layer — Claude skill

Thin pointer into [`adapters/claude/`](../../adapters/claude/).


## Charter

Before treating this as a worker queue, preserve the repo charter: delegation is a mechanism; continuity is the product. Read [`../../CHARTER.md`](../../CHARTER.md) for the product hierarchy and milestone rule.

## When invoked at session start

1. Read project registry (read-only).
2. If the current working directory matches a project, load its `context.md` and `decisions.md` into context.
3. Check for any worker tasks targeted at this device. Do **not** auto-claim — surface the list and let the human decide.

## When invoked to execute a task

1. Validate the incoming task against `core/schemas/worker-task.schema.json`.
2. Re-check `trust_level` against local trust policy. Refuse if mismatched.
3. Honor `files_allowed`. Refuse to write outside it.
4. Produce the artifacts in `expected_artifacts`.
5. Append a one-line decision entry per `core/decision-log.md`.
6. Submit result. Mark `needs_human_approval: true` if anything happened that wasn't covered by the original instruction.

## What this skill MUST NOT do

- Do not write to the project registry. OpenClaw owns it.
- Do not rewrite `history.md`. OpenClaw owns it.
- Do not auto-sync skills or settings from the VM.
- Do not escalate trust silently. Always surface to OpenClaw.

## Status

Stub.
