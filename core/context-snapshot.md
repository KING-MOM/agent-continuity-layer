# Project Context Snapshot

_Generated: 2026-05-28T02:02:38Z from 15653022ac58e5d66d87b27f0255d91067a39285_

> This is a generated snapshot. Do not edit by hand — run `scripts/context.sh --write` to refresh. The one operator-maintained field is `next_safe_action`, sourced from `core/context-pinned.json`.

## Identity

- **Project**: agent-continuity-layer
- **Charter**: Durable continuity for AI agents across sessions, tools, devices, and model providers.
- **Repo**: `~/.openclaw/workspace/agent-continuity-layer`
- **Branch**: `main`
- **HEAD**: `15653022ac58e5d66d87b27f0255d91067a39285`

## Milestone State

- **Last completed**: unknown
- **Next major milestone** (per roadmap): (none listed beyond last_completed)
- **Milestone rule**: Every milestone must strengthen at least one charter continuity primitive; delegation-only work belongs in adapters.

_The roadmap tracks majors only. The next concrete sub-slice (e.g. M7.1) lives in **Next Safe Action** below, sourced from `core/context-pinned.json`._

## Next Safe Action

bootstrap.sh gained the --connect-all opt-in flag. Adoption story now has two clean variants: one command for install + wire (curl … | bash -s -- --connect-all), or default two-step for users who want explicit consent on third-party config writes. The default stays conservative so a plain curl-pipe never touches Claude Desktop / Cursor / Zed configs without the operator typing the flag. README and docs/install.md updated to show both paths. Bumping to v0.1.6 because public install UX changed. After release, next active arc choices remain: M14.1 cross-project queries, or M14.2 per-project context pin (schema bump candidate). Out of scope still: release signing, multi-tenant trust, cryptographic identity.

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
| `1565302` | — | — | chore: initialize sanitized public history |

