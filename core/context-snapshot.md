# Project Context Snapshot

_Generated: 2026-06-04T22:44:34Z from 5c1130b3d25ecaf2a3b906cc4a139b2b79829feb_

> This is a generated snapshot. Do not edit by hand — run `scripts/context.sh --write` to refresh. The one operator-maintained field is `next_safe_action`, sourced from `core/context-pinned.json`.

## Identity

- **Project**: agent-continuity-layer
- **Charter**: Durable continuity for AI agents across sessions, tools, devices, and model providers.
- **Repo**: `~/.openclaw/workspace/agent-continuity-layer`
- **Branch**: `main`
- **HEAD**: `5c1130b3d25ecaf2a3b906cc4a139b2b79829feb`

## Milestone State

- **Last completed**: M17.0
- **Next major milestone** (per roadmap): (none listed beyond last_completed)
- **Milestone rule**: Every milestone must strengthen at least one charter continuity primitive; delegation-only work belongs in adapters.

_The roadmap tracks majors only. The next concrete sub-slice (e.g. M7.1) lives in **Next Safe Action** below, sourced from `core/context-pinned.json`._

## Next Safe Action

Second slice of M17 (heuristic transcript compile) shipped: `agent-continuity transcript compile <id> [--apply]` reads Claude Code session JSONLs and appends structured decision entries to the canonical decisions.jsonl. Privacy invariant load-bearing — compiled entries never include raw chat content, never include tool_use input free-form fields (new_string/content/prompt), only structured tool call metadata. Privacy denylist drops events with sensitive file paths (credentials/, .env, .ssh, etc.) or Bash command secret patterns (sk-*, ghp_*, AKIA*, BEGIN PRIVATE KEY). Heuristics cover git commits, releases, tags, package installs, file edits to load-bearing dirs, AskUserQuestion events, and operator-explicit `decisions add` invocations. Idempotent: re-compile is no-op (deterministic sha256 ids via session-time ts). Validated against the real local session 15083edc (this conversation): 566 candidates extracted (305 file-edits, 144 writes, 53 git-commits, 36 ask-user-questions, 14 operator-explicit, 7 git-tags, 6 github-releases, 1 package-install), 0 privacy hits (clean). M17 arc now 2/3 complete — only M17.2 LLM-based summary remains for purely conversational decisions that have no tool follow-through. M14.1 cross-project queries still pinned as future.

## Navigation

- [CHARTER.md](../CHARTER.md) — charter
- [docs/roadmap.md](../docs/roadmap.md) — roadmap
- [docs/architecture.md](../docs/architecture.md) — architecture
- [docs/handoff-vs-continuity.md](../docs/handoff-vs-continuity.md) — handoff vs continuity
- [docs/milestone-template.md](../docs/milestone-template.md) — milestone template

## Non-Goals

_Verbatim from CHARTER.md — what this project is deliberately not:_

- This is not only an OpenClaw plugin.
- This is not only a worker queue.
- This is not an autonomous-agent runner.
- This does not make the VM a config authority.
- This does not give agents broad write authority without trust policy and audit.

## Work In Flight

| state | count |
|---|---|
| queued | 0 |
| claimed | 0 |
| running | 0 |
| awaiting-approval | 0 |
| completed | 1 |
| rejected | 1 |
| failed | 0 |
| cancelled | 0 |

_No open tasks._

## Trust

- **Grants**: 1
- **Default policy**: default allows 4 kind(s): code-review, data-extraction, explain, research
- **Soonest expiry**: 2026-06-01T23:59:59Z

## Recent Activity

| sha | milestone | primitive | subject |
|---|---|---|---|
| `5c1130b` | M17.0 | — | M17.0: local Claude Code transcript index — read-only inventory |
| `801df15` | M15.1.1 | — | M15.1.1: surface build reference + cross-environment reproducibility smoke |
| `13ff7bf` | — | — | docs: scrub handoff path examples |
| `b8f6a82` | — | — | docs: fix README status and MCP wording |
| `1c0656a` | — | — | docs: frame continuity as compiled agent memory |

