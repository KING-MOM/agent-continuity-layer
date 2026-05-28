---
name: agent-continuity
version: "0.1.0"
role: openclaw
description: "OpenClaw-side bootstrap for the agent-continuity-layer. Loads the project registry, resolves the active project, enqueues worker tasks for Claude/Codex, and enforces trust policy at the boundary. Triggers on every OpenClaw session start and on every inbound channel message that requires worker delegation."
---

# Agent Continuity Layer — OpenClaw skill

This skill is a thin pointer into [`adapters/openclaw/`](../../adapters/openclaw/). It does not duplicate logic — it tells OpenClaw what the adapter does and how to invoke it.


## Charter

Before treating this as a worker queue, preserve the repo charter: delegation is a mechanism; continuity is the product. Read [`../../CHARTER.md`](../../CHARTER.md) for the product hierarchy and milestone rule.

## On session start

1. Load `core/schemas/project-registry.schema.json` from the VM.
2. Match the current channel/context to a project (or create one).
3. Load `context.md` + `decisions.md` for that project.
4. Load the local `trust-policy.json` (validated against `core/schemas/trust-policy.schema.json`).

## On inbound work that needs a worker

1. Construct a `worker-task` object (see `core/schemas/worker-task.schema.json`).
2. Resolve `trust_level` against the trust policy. If the task exceeds policy, set status to `awaiting-approval` and notify the human.
3. Enqueue. The worker adapter on the target machine will claim it.
4. Audit every transition.

## What this skill MUST NOT do

- Do not write to workspace repos. That's the worker.
- Do not bypass the queue and run a worker inline.
- Do not auto-sync `~/.claude/skills/` or `~/.codex/skills/` from the VM. Git owns those.

## Status

Stub. Wire-up to the OpenClaw daemon is the first implementation milestone.
