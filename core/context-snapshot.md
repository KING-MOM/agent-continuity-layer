# Project Context Snapshot

_Generated: 2026-06-04T16:42:57Z from 801df15fcbef46342849eff310e28dc0be66f5cc_

> This is a generated snapshot. Do not edit by hand — run `scripts/context.sh --write` to refresh. The one operator-maintained field is `next_safe_action`, sourced from `core/context-pinned.json`.

## Identity

- **Project**: agent-continuity-layer
- **Charter**: Durable continuity for AI agents across sessions, tools, devices, and model providers.
- **Repo**: `~/.openclaw/workspace/agent-continuity-layer`
- **Branch**: `main`
- **HEAD**: `801df15fcbef46342849eff310e28dc0be66f5cc`

## Milestone State

- **Last completed**: M16.0
- **Next major milestone** (per roadmap): (none listed beyond last_completed)
- **Milestone rule**: Every milestone must strengthen at least one charter continuity primitive; delegation-only work belongs in adapters.

_The roadmap tracks majors only. The next concrete sub-slice (e.g. M7.1) lives in **Next Safe Action** below, sourced from `core/context-pinned.json`._

## Next Safe Action

First slice of M17 (local transcript index) shipped: `agent-continuity transcript list/show/path` indexes Claude Code session JSONL files under ~/.claude/projects/ — read-only inventory exposing session_id, ai_title, cwd, git_branch, started/last, duration, message counts, tool_call breakdown, and JSONL path. Pure stdlib, no network, no writes to substrate. Validates against real local sessions (this conversation comes up correctly tagged 'Design life-agents-unified infrastructure', 285h, 4365 messages, 1513 tool calls). Charter primitive strengthened: handoff ledger + project registry, extended with session-level awareness. Next slices in M17: M17.1 heuristic compile (read JSONL → append structured decisions to canonical log → leverages M10 sync for cross-device propagation), M17.2 LLM-based session summary (operator opt-in, real API cost per session). Decision on whether to proceed with M17.1 depends on what the local index reveals about the shape of extractable signal in real transcripts.

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
| `801df15` | M15.1.1 | — | M15.1.1: surface build reference + cross-environment reproducibility smoke |
| `13ff7bf` | — | — | docs: scrub handoff path examples |
| `b8f6a82` | — | — | docs: fix README status and MCP wording |
| `1c0656a` | — | — | docs: frame continuity as compiled agent memory |
| `5b2c40c` | — | — | release: v0.2.0 |

