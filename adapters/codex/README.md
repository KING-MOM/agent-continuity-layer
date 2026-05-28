# adapters/codex

**Role:** Worker.

This adapter teaches Codex CLI how to:
1. Read project context (read-only).
2. Claim a worker task.
3. Execute repo changes within `trust_level` + `files_allowed` + `branch`.
4. Run tests / type checks if `kind: test-run` or as part of `code-change` verification.
5. Submit a patch (or commit + push, if trust allows) plus a decision entry.

## What Codex is good for

- Mechanical edits, multi-file renames.
- Test loops, build loops.
- Shell-heavy repo execution.
- Anything where "just run it and report what happened" is the right shape.

## What Codex is NOT for (route to Claude instead)

- Reasoning-heavy review.
- Briefs, copy, narrative summaries.
- Open-ended design discussions.

## Interfaces

Same shape as the Claude adapter — see [`../claude/README.md`](../claude/README.md). The differences live in:
- Default `target.preferred_model` is a Codex model.
- Default `trust_level` ceiling is `scoped-write` (vs Claude's `read-only`) since Codex is purpose-built for repo execution.

## Status

Stub.
