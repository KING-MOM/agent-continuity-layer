# Project Context Snapshot

_Generated: 2026-05-28T02:26:30Z from 058f952b61d21108b8e7edc9a0cca3ee4e24869a_

> This is a generated snapshot. Do not edit by hand — run `scripts/context.sh --write` to refresh. The one operator-maintained field is `next_safe_action`, sourced from `core/context-pinned.json`.

## Identity

- **Project**: agent-continuity-layer
- **Charter**: Durable continuity for AI agents across sessions, tools, devices, and model providers.
- **Repo**: `~/.openclaw/workspace/agent-continuity-layer`
- **Branch**: `main`
- **HEAD**: `058f952b61d21108b8e7edc9a0cca3ee4e24869a`

## Milestone State

- **Last completed**: unknown
- **Next major milestone** (per roadmap): (none listed beyond last_completed)
- **Milestone rule**: Every milestone must strengthen at least one charter continuity primitive; delegation-only work belongs in adapters.

_The roadmap tracks majors only. The next concrete sub-slice (e.g. M7.1) lives in **Next Safe Action** below, sourced from `core/context-pinned.json`._

## Next Safe Action

First slice of the M15 release-integrity arc landed: SECURITY.md (vuln disclosure via GitHub Private Vulnerability Reporting, 90-day coordinated disclosure, aspirational best-effort SLA, in-scope vs out-of-scope explicit) and docs/versioning.md (SemVer commitment, three API surfaces defined: adapter contract + CLI + schemas, deprecation policy, 10 criteria for v1.0 with no date commitment, LTS intent documented). PVR enabled on the repo. No code changes, no release bump — docs slice. Next active slice in M15: M15.1 reproducible builds (release.sh produces deterministic tarballs; release-smoke verifies rebuild yields identical sha256; documented in install.md as precursor to signed releases). Out of scope for v0.1.x stays: signed releases (M15.3), multi-tenant trust, cryptographic identity. Tier 2+3 enterprise gaps (SOC2, E&O insurance, vendor entity, bus factor mitigation) are explicitly NOT technical work and remain out of agentic scope.

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
| `058f952` | — | — | feat(adapters): add named web model adapter tokens |
| `85f2384` | — | — | release: v0.1.6 |
| `c190f30` | — | — | feat(bootstrap): --connect-all opt-in flag for one-command install + wire |
| `1565302` | — | — | chore: initialize sanitized public history |

