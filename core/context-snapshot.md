# Project Context Snapshot

_Generated: 2026-05-28T05:12:00Z from 4297ad20d0f51cd8b59051f41b6ea206016dda1b_

> This is a generated snapshot. Do not edit by hand — run `scripts/context.sh --write` to refresh. The one operator-maintained field is `next_safe_action`, sourced from `core/context-pinned.json`.

## Identity

- **Project**: agent-continuity-layer
- **Charter**: Durable continuity for AI agents across sessions, tools, devices, and model providers.
- **Repo**: `~/.openclaw/workspace/agent-continuity-layer`
- **Branch**: `main`
- **HEAD**: `4297ad20d0f51cd8b59051f41b6ea206016dda1b`

## Milestone State

- **Last completed**: M15.1
- **Next major milestone** (per roadmap): (none listed beyond last_completed)
- **Milestone rule**: Every milestone must strengthen at least one charter continuity primitive; delegation-only work belongs in adapters.

_The roadmap tracks majors only. The next concrete sub-slice (e.g. M7.1) lives in **Next Safe Action** below, sourced from `core/context-pinned.json`._

## Next Safe Action

First slice of M16 (device-to-device handoff) shipped: `agent-continuity handoff export/import/inspect` packages substrate state (decisions, registry, trust policy, queue) and optionally Claude Code session transcripts into a tar.gz. Default export is state-only; --include-claude opts into transcript transfer with a path-encoding guard that skips claude restoration when source HOME != target HOME. Import backs up existing state under XDG_DATA_HOME unless --no-backup. New M-arc separate from M15 release-integrity (which still has M15.2 SBOM + M15.3 cosign signed releases pending). Next slices to choose between: M15.2 SBOM (Tier 1 enterprise readiness), M15.3 cosign (closes Tier 1; bumps to v0.2.0), or cross-username Claude path rewrite as a follow-up to this slice. M14.1 cross-project queries still pinned as future.

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
| `4297ad2` | — | — | docs: add security and versioning policy |
| `9e512ef` | — | — | release: v0.1.7 |
| `30bb5cd` | M15.1 | — | M15.1 polish: smoke verifies reproducibility + docs explain how to verify |
| `f7ae48e` | M15.1 | — | M15.1: reproducible builds — deterministic tarballs via _repro_tar.py |
| `2f9c295` | M15.0 | — | M15.0: SECURITY.md + versioning policy — release integrity arc opens |

