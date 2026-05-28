# Agent Continuity Layer Charter

## Primary Goal

Durable continuity for AI agents across sessions, tools, devices, and model providers.

Any agent should be able to enter a project, recover context, understand prior decisions, perform bounded work, and leave an auditable handoff for the next agent.

## Product Hierarchy

1. Continuity: project memory, context, decisions, history, session recovery.
2. Trust: who can do what, where, under which approval.
3. Handoff: task ledger, artifacts, result summaries, state transfer.
4. Delegation: assigning bounded work to Claude, Codex, OpenClaw, or future agents.
5. Adapters: local CLI, web agents, OpenClaw, MCP, VM, browser, and future hosts.

Delegation is a mechanism. Continuity is the product.

## Continuity Primitives

Every milestone should strengthen at least one of these primitives:

- Project registry: what projects exist, where they live, and how agents find them.
- Context recovery: what a new agent needs to know to become useful quickly.
- Decision log: why choices were made, not just what changed.
- History: what happened across sessions, channels, and devices.
- Trust policy: what each agent may do without re-asking the operator.
- Handoff ledger: what work was delegated, claimed, completed, blocked, or reviewed.
- Artifact memory: patches, reports, test output, receipts, screenshots, and summaries.
- Adapter portability: the same continuity layer works across local, web, and orchestrated agents.

## Non-Goals

- This is not only an OpenClaw plugin.
- This is not only a worker queue.
- This is not an autonomous-agent runner.
- This does not make the VM a config authority.
- This does not give agents broad write authority without trust policy and audit.

## Architecture Rules

- Git owns executable code, schemas, docs, and migrations.
- Runtime state lives in host-local config/cache or a pinned remote memory backend.
- The VM may store memory, but it must not silently distribute executable agent behavior.
- Workers produce artifacts and audit trails; humans or policy approve dangerous transitions.
- If a proposed milestone improves delegation but not continuity, trust, or handoff, it must explicitly justify why it belongs here.

## Milestone Rule

Every milestone proposal must answer:

- Which continuity primitive does this strengthen?
- What does a new agent know after this that it did not know before?
- What handoff artifact survives after the session ends?
- What trust or approval boundary changed?
- What is deliberately out of scope?

If those answers are weak, the milestone is probably infrastructure drift.

## See Also

- [`docs/handoff-vs-continuity.md`](docs/handoff-vs-continuity.md) — what M4/M5 actually built, and why the worker queue is a subsystem, not the product
- [`docs/roadmap.md`](docs/roadmap.md) — continuity-first milestone sequence
- [`docs/milestone-template.md`](docs/milestone-template.md) — proposal shape every new milestone must answer
