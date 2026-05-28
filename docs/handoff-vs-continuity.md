# Handoff vs Continuity — what M4 and M5 actually built

> **M4/M5 are the handoff subsystem. They exist to preserve continuity across delegation, not to make this project an autonomous runner.**

This document exists because the bulk of this repo's recent commits (M4.0 through M5.5) are about a worker queue, trust policy, a bridge to `agent-worker.mjs`, and an audit chain. A future reader skimming git log could easily conclude *"this is a worker queue"* and miss that the worker queue is one mechanism inside a larger continuity layer.

If that misreading sticks, M6+ proposals drift toward execution mechanics and the project quietly becomes what the charter explicitly rules out.

## What this project is (from the charter)

Per [`../CHARTER.md`](../CHARTER.md):

> Durable continuity for AI agents across sessions, tools, devices, and model providers.
>
> Any agent should be able to enter a project, recover context, understand prior decisions, perform bounded work, and leave an auditable handoff for the next agent.

The charter's **product hierarchy** is ordered, and the order matters:

1. **Continuity** — project memory, context, decisions, history, session recovery
2. **Trust** — who can do what, where, under which approval
3. **Handoff** — task ledger, artifacts, result summaries, state transfer
4. **Delegation** — assigning bounded work to Claude, Codex, OpenClaw, or future agents
5. **Adapters** — local CLI, web agents, OpenClaw, MCP, VM, browser, and future hosts

Delegation is #4. The worker queue lives there. M4/M5 spent most of their effort on layers 3 and 4 because that's the part that was missing infrastructure when this work began.

## What M4 and M5 actually built

| Milestone | What landed | Charter primitive strengthened |
|---|---|---|
| M4.0 | `scripts/worker.sh` queue with enqueue/list/show/claim/submit; policy enforced at enqueue and re-checked at claim; audit trail per task | handoff ledger, trust policy |
| M4.1 | Race-safe claim via `os.rename` as exclusion primitive; adapter + worker-ownership enforcement | handoff ledger (correctness) |
| M4.2 | `approve` / `reject` subcommands for the `awaiting-approval` state; `expected_artifacts` validation at submit | handoff ledger, trust policy |
| M4.3 | Disposable fixture repo + first repo grant in the host policy | trust policy |
| M4.4 | First real cross-vendor task: Codex executed against the fixture, full audit chain | handoff ledger (proven end-to-end) |
| M4.5 | Claude reviewed the M4.4 patch read-only and produced a report | handoff ledger (read-only worker path) |
| M4.6 | `adapters/openclaw/queue_client.py` — typed Python contract for OpenClaw daemon | adapter portability |
| M5.0 → M5.5 | Bridged `agent-worker.mjs` to `worker.sh` for enqueue / list / show / trust-* and runTask + nextPendingId; preserved Mika MCP compatibility; M5.5 decided to keep the bridge rather than port `.mjs` execution into Python | handoff ledger, adapter portability |

Every primitive these milestones strengthened is **handoff ledger** or **trust policy** or **adapter portability** — three of the eight charter primitives. The other five (project registry, context recovery, decision log, history, artifact memory) saw light or no progress in M4/M5.

## What it does NOT make this project be

The charter's **Non-Goals**:

- This is not only an OpenClaw plugin.
- **This is not only a worker queue.**
- **This is not an autonomous-agent runner.**
- This does not make the VM a config authority.
- This does not give agents broad write authority without trust policy and audit.

`agent-worker.mjs` is the autonomous-runner-adjacent code, and it lives intentionally outside this repo. The bridge built in M5 routes its data plane through this layer for canonical state, audit, and trust — but the execution loop, prompt construction, and subprocess management stay in `.mjs`. M5.5 recorded the decision to keep it that way (see `docs/openclaw-integration-plan.md`'s M5.5 section).

## How to read M6 and beyond

[`docs/roadmap.md`](roadmap.md) lists M6–M11 explicitly as continuity-first milestones (charter enforcement, project context recovery, cross-agent decision writeback, web/local adapter pattern, VM-backed multi-device sync, OSS quickstart). None of them are "make the worker queue do more."

The [`milestone-template.md`](milestone-template.md) and the charter's **Milestone Rule** are the operational guardrail:

> If a proposed milestone improves delegation but not continuity, trust, or handoff, it must explicitly justify why it belongs here.

When a future proposal sounds like *"add subprocess management to ..."* or *"port `.mjs` execution into Python because it would consolidate code"*, the milestone rule asks: which continuity primitive does this strengthen? If the honest answer is *"none, it's engineering tidiness"*, the proposal belongs in an adapter or runner project, not this layer's core.

That's the test M5.5 applied to defer the Replace decision indefinitely. The same test applies to any future M-series proposal.

## Where to find the things M4/M5 built

- Worker queue + state machine: [`scripts/worker.sh`](../scripts/worker.sh) + [`scripts/_worker.py`](../scripts/_worker.py)
- Schemas: [`core/schemas/worker-task.schema.json`](../core/schemas/worker-task.schema.json), [`core/schemas/worker-result.schema.json`](../core/schemas/worker-result.schema.json), [`core/schemas/trust-policy.schema.json`](../core/schemas/trust-policy.schema.json)
- Trust grants CLI: `worker.sh trust-add / trust-list / trust-check / trust-remove`
- OpenClaw Python client: [`adapters/openclaw/queue_client.py`](../adapters/openclaw/queue_client.py)
- `.mjs` bridge: `~/.openclaw/workspace/scripts/agent-worker.mjs` (host-side, with backups at `.bak-pre-m5-2b`)
- Doctor checks for queue depth + trust policy + worker bridge: [`scripts/_doctor.py`](../scripts/_doctor.py)
- M5 integration plan (and the M5.5 decision record): [`openclaw-integration-plan.md`](openclaw-integration-plan.md)
- Evidence: [`artifacts/`](artifacts/) — per-milestone evidence per the M5.2b convention

The handoff subsystem is solid and well-tested. The continuity layer it sits inside is still being built. M6+ is that work.

## See also

- [`../CHARTER.md`](../CHARTER.md) — the canonical product charter (continuity primitives, non-goals, milestone rule)
- [`roadmap.md`](roadmap.md) — what comes next, ordered by continuity primitive
- [`milestone-template.md`](milestone-template.md) — the proposal shape every new milestone must answer
- [`architecture.md`](architecture.md) — system structure, milestones, and the M5 chain in detail
