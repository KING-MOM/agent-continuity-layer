---
name: agent-continuity
version: "0.1.0"
role: codex
description: "Codex CLI-side worker for the agent-continuity-layer. Reads project context (read-only), claims worker tasks dispatched by OpenClaw, executes bounded repo changes within trust_level + files_allowed + branch, runs tests, and submits artifacts back. Triggers when OpenClaw dispatches a task targeting Codex, or when the user runs `codex` inside a registered project directory."
---

# Agent Continuity Layer — Codex skill

Thin pointer into [`adapters/codex/`](../../adapters/codex/).


## Charter

Before treating this as a worker queue, preserve the repo charter: delegation is a mechanism; continuity is the product. Read [`../../CHARTER.md`](../../CHARTER.md) for the product hierarchy and milestone rule.

## When invoked at session start

1. Read project registry (read-only).
2. If `cwd` matches a project's `local_paths`, load `context.md` + `decisions.md`.
3. List worker tasks targeted at this device. Do not auto-claim.

## When executing a task

Same flow as the Claude worker (see [`../claude/SKILL.md`](../claude/SKILL.md)). Differences:
- Default operation is a code change on a feature branch derived from `input.branch`.
- For `kind: test-run`, the artifact is a `test-result` report; no patches expected.
- `trust_level: scoped-write` is the normal ceiling — escalation to `repo-write` (e.g. for committing + pushing) requires explicit policy.

## What this skill MUST NOT do

- Do not push to `main` / `master` / `dev` unless `trust_level: elevated` AND the policy explicitly allows it on this repo.
- Do not write to the project registry.
- Do not auto-sync skills from the VM.

## Status

Stub.
