# Project Context Snapshot

_Generated: 2026-05-28T05:31:24Z from 0f363b3ee7269a6edad619a8ad3fa5e3b5e07d4a_

> This is a generated snapshot. Do not edit by hand — run `scripts/context.sh --write` to refresh. The one operator-maintained field is `next_safe_action`, sourced from `core/context-pinned.json`.

## Identity

- **Project**: agent-continuity-layer
- **Charter**: Durable continuity for AI agents across sessions, tools, devices, and model providers.
- **Repo**: `~/.openclaw/workspace/agent-continuity-layer`
- **Branch**: `main`
- **HEAD**: `0f363b3ee7269a6edad619a8ad3fa5e3b5e07d4a`

## Milestone State

- **Last completed**: M16.0
- **Next major milestone** (per roadmap): (none listed beyond last_completed)
- **Milestone rule**: Every milestone must strengthen at least one charter continuity primitive; delegation-only work belongs in adapters.

_The roadmap tracks majors only. The next concrete sub-slice (e.g. M7.1) lives in **Next Safe Action** below, sourced from `core/context-pinned.json`._

## Next Safe Action

Final slice of the M15 release-integrity arc shipped: cosign keyless OIDC signed releases via GitHub Actions workflow at .github/workflows/release.yml. Bumping to v0.2.0 because the install path changed (breaking): bootstrap.sh and install.sh now require cosign to verify signatures by default, refusing without --no-verify escape. Workflow triggers on v* tag push, builds tarball + sha256 + SBOM, signs each (plus bootstrap.sh) with cosign keyless OIDC, creates the GitHub Release with all signed artifacts. Tier 1 enterprise readiness items now ALL closed: LICENSE, CONTRIBUTING, SECURITY.md, versioning policy, reproducible builds, CycloneDX SBOM, signed releases. Next active arc choices: M14.1 cross-project queries, M16.1 cross-username Claude path rewrite, or production-readiness Tier 2/3 (multi-tenant trust, cryptographic adapter identity, tamper-evident decision log) — but those require commercial entity + team + capital, not technical work alone.

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
| `0f363b3` | — | — | release: v0.1.9 |
| `296fa35` | M15.2 | — | M15.2: CycloneDX 1.5 SBOM per release |
| `9661da3` | — | — | release: v0.1.8 |
| `51d390f` | M16.0 | — | M16.0: device-to-device handoff — export/import/inspect |
| `4819121` | — | — | release: v0.1.7 |

