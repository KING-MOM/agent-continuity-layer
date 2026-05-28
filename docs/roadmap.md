# Continuity-First Roadmap

This roadmap keeps the project centered on continuity across agents, sessions, tools, devices, and model providers.

Delegation remains useful, but it is one mechanism inside handoff. The product is durable continuity.

## Completed Foundation

| Milestone | Status | Continuity primitive strengthened |
|---|---|---|
| M0 Scaffold | Done | Repository shape, schemas, adapter boundaries |
| M1 Doctor | Done | Operator confidence, drift detection |
| M2 Thin skills | Done | Agent onboarding, portable instructions |
| M3 Read-only sync | Done | Context recovery, cross-device memory cache |
| M4 Worker queue/control plane | Done | Handoff ledger, trust policy, artifacts, audit |
| M5 OpenClaw bridge | Done (M5.5: bridge wins; port deferred) | Adapter portability, canonical task/trust state, handoff ledger |

## Continuity Milestones (M6-M14)

### M6 - Charter Enforcement + Memory Inventory

**Status: Shipped.**

Goal: make continuity primitives visible to tools and agents.

Deliverables:

- `CHARTER.md` referenced from README, architecture, and skills.
- Machine-readable inventory of project memory files and their ownership rules.
- Doctor check that reports whether the charter exists and whether skills point to it.
- Migration note that M4/M5 delegation work is a handoff subsystem, not the whole product.

A new agent should know: what this project is for, which memory files matter, and how not to confuse delegation with the product.

### M7 - Project Context Recovery

**Status: Shipped.**

Goal: make a fresh agent useful in under a minute.

Deliverables:

- Standard `context.md` sections for current goal, repo map, open decisions, active risks, and next safe action.
- `sync.sh` validation that context files contain the required sections.
- `doctor.sh` surfaces stale or missing project context.
- Example local-only project context for people without a VM.

A new agent should know: where the project is, what changed recently, what is risky, and what to do next.

### M8 - Cross-Agent Decision Writeback

**Status: Shipped.**

Goal: preserve why work happened across Claude, Codex, OpenClaw, and future agents.

Deliverables:

- Append-only decision entry command with source adapter and optional task ID.
- Rules for when workers may append decisions vs request approval.
- Decision compaction strategy for long-running projects.
- Tests that worker output can create a decision artifact without mutating unrelated memory.

A new agent should know: what decisions were made and why, without reading every prior chat.

### M9 - Web/Local Agent Adapter Pattern

**Status: Shipped.**

Goal: support both terminal agents and web agents without binding the project to one host.

Spec: [`docs/m9-adapter-pattern.md`](m9-adapter-pattern.md)

Deliverables:

- M9.0: six-operation adapter contract plus identity and bundle schemas.
- M9.1: operator-mediated bundle export/ingest for web agents.
- M9.2: MCP tool surface over the same operations.
- M9.3: doctor checks for adapter identity, bundle integrity, and transport visibility.
- M9.4: walkthroughs for ChatGPT web, Claude web, Codex local, Mika/OpenClaw, and read-only auditors.

A new agent should know: how to join continuity whether it runs locally, in a browser, or behind an orchestrator.

### M10 - VM-Backed Multi-Device Sync v1

**Status: Shipped — except M10.3 (real-VM happy-path verification, gated on real VM availability).**

Goal: make continuity portable across machines without making the VM an executable-code authority.

Deliverables:

- Real VM happy-path verification for host-key pinned sync.
- Project registry reconciliation across two devices.
- Conflict behavior documented for context, decisions, and history.
- Device attribution in sync metadata.

A new agent should know: whether local memory is current and which device last wrote each continuity artifact.

### M11 - OSS Quickstart

**Status: Shipped.**

Goal: make the project usable without Mika/OpenClaw.

Deliverables:

- Local-only quickstart using a fixture repo.
- One Codex worker flow and one Claude worker flow.
- Trust policy starter with safe defaults.
- Example orchestrator that enqueues and reads tasks without OpenClaw.

A new user should understand: the project is an agent continuity layer, not a Mika-only internal tool.


### M12 - Packaging / Release

**Status: Shipped — closed in v0.1.0.**

Goal: make the continuity layer installable and upgradeable on a clean machine.

Deliverables:

- Versioned release process for schemas, scripts, skills, and migrations.
- Installer or bootstrap command that sets up local state/config safely.
- Migration runner for host-state and host-config changes across versions.
- Clean-machine smoke test that proves doctor, context, decisions, and one handoff path work after install.

A new user should know: how to install, upgrade, verify, and roll back the continuity layer without reading the source tree.

### M13 - Agent SDK / MCP Surface

**Status: Shipped — MCP stdio server in v0.1.1, Python SDK + reference agent in v0.1.2, unified adapter connect in v0.1.3, per-host connect targets in v0.1.4.**

Goal: expose continuity primitives through a stable API instead of shell-only scripts.

Deliverables:

- MCP/tool surface for reading context, appending decisions, listing handoffs, and checking trust.
- Small SDK wrapper around the same operations for local orchestrators and future adapters.
- Unified `agent-continuity connect all` path so installed hosts can wire Claude Desktop, Cursor, Zed, local skills, and OpenClaw bridge visibility in one operator-mediated step.
- Stable request/response schemas mapped to the existing JSON Schemas.
- Compatibility tests proving shell, MCP, and SDK paths produce the same continuity artifacts.

A new agent should know: how to participate in continuity through a standard interface even when it cannot shell out directly.

### M14 - Multi-Project Registry

**Status: Active — next arc.**

Goal: make continuity work across multiple repos and projects, not just the current working tree.

Deliverables:

- Canonical project registry with project id, repo path(s), memory locations, active branch, and last-seen metadata.
- Project matching rules for local repos, VM-backed sessions, and web-agent import/export bundles.
- Cross-project queries for context snapshots, decisions, and handoff ledgers.
- Doctor checks for registry drift, missing projects, duplicate identities, and stale paths.

A new agent should know: which project it is in, where related continuity memory lives, and how to avoid mixing decisions across repos.

## Out of v0.1.x Scope

The items below are deliberately NOT on the v0.1.x roadmap. Documented here so future contributors and readers know they are deferred-with-reason, not forgotten.

### Release signing

`agent-continuity-vX.Y.Z.sha256` attachments give corruption detection only. Cryptographic release signing (GPG, sigstore, minisign) is a discrete release-engineering slice. It ships when adoption signal asks for it — the first concrete user requirement, not preemptively. Until then, `docs/install.md` and release notes are explicit that integrity ≠ publisher identity.

### Cryptographic adapter identity / multi-tenant trust

`docs/trust-policy.md` declares these as design limits for v0.1.x: identity is descriptive (`claude`, `codex`, `openclaw`, `human`), not signed; trust policy is single-operator per-device. Multi-operator and cross-org trust would look meaningfully different from anything we could design speculatively today, so they stay as documented limits, not roadmap promises. If a real customer requirement surfaces, that requirement defines the design.

## Guardrail

If future milestones are mostly about task execution, subprocess management, or queue mechanics, they must state which continuity primitive they strengthen. Otherwise they belong in an adapter or runner project, not the continuity layer core.
