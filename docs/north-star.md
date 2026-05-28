# North Star

> An AI agent — any model, any host, any device — can pick up a project mid-flight and contribute durably. Continuity is **portable, attributable, auditable, and adapter-agnostic.** The operator stays in control of authority; the substrate stays out of the way.

This doc is the long-term shape of the project, read in two minutes. Use it when you're trying to decide whether a proposal belongs here at all. Use [`../CHARTER.md`](../CHARTER.md) when you need the rules; use [`roadmap.md`](roadmap.md) when you need the sequence; use this when you need the *direction*.

## What this exists to fix

Every AI-agent project today reinvents the same five wheels:

1. **Memory** — the operator has to re-explain the project on every new session, with every new model, in every new chat surface.
2. **Provenance** — reasoning evaporates the moment the chat ends; the next agent has the *what* (the diff, the commit) but not the *why*.
3. **Handoff** — work passed to a delegated worker has no auditable trail back; the operator either trusts blindly or re-reviews everything.
4. **Authority** — there's no shared notion of "this adapter is allowed to do this kind of work on this repo," so trust is binary and per-session.
5. **Device portability** — memory lives on whichever laptop the operator was using; the second device starts from zero.

A real continuity layer fixes all five. Tooling that fixes one and ignores the others is infrastructure drift.

## What we're aiming at

A substrate where:

- **A fresh agent can become useful in under 60 seconds.** Read one snapshot file, know identity, milestone state, work in flight, what to do next. (M7.)

- **Reasoning is durable across agents.** Workers and operators append decisions to a single content-addressed log. Future agents inherit the *why*, not just the diff. (M8.)

- **Adapters are interchangeable transports.** Six operations — `whoami`, `read_context`, `read_decisions`, `append_decision`, `claim_task`, `submit_result` — work the same through local shell, MCP, the OpenClaw bridge, or operator-mediated JSON bundles for web agents. (M9.)

- **Memory follows the operator across devices.** Decisions, project identity, and operator intent (the pin) sync through an operator-controlled VM. Trust policy never syncs; each device decides what it allows. (M10.)

- **Doctor tells the operator what's true.** No auto-resolution. No silent retries. The health check surfaces drift, stale state, and authority gaps so the operator can act explicitly. (Throughout.)

- **Git owns code; the VM owns memory.** The two never trade places. (Charter rule.)

## What we're explicitly not

This is the negative-space version of the charter's non-goals. Worth repeating because the gravity of every adjacent system pulls in these directions.

- **Not a worker queue.** M4/M5 built one to enable delegated handoff; the queue is a subsystem, not the product. (See [`handoff-vs-continuity.md`](handoff-vs-continuity.md).)

- **Not an autonomous-agent runner.** The execution loop, subprocess management, and prompt construction live in adapter projects (OpenClaw, Claude Code, Codex CLI). This layer is the *substrate*; runners are clients.

- **Not a VM as code authority.** The VM stores memory artifacts. It does not ship scripts, configs, skills, or trust policy. Operators on each device retain authority. (Charter non-goal; M10's hard invariant.)

- **Not a web platform.** Web agents participate via operator-mediated bundles. They join continuity from inside a chat surface. The layer has no hosted UI in its definition.

- **Not a closed system.** Bundles, MCP manifests, and the schema set are all checked into git and consumable by any adapter implementation. OSS quickstart (M11) is the moment this stops being "the original operator setup" and starts being a thing anyone can adopt.

## How we know we're getting there

The eight continuity primitives from the charter are the measurement axis. Every milestone strengthens at least one. Anything else is infrastructure drift.

| Primitive | Where it lives today |
|---|---|
| Project registry | M10.1 — per-project records, structured cross-device merge |
| Context recovery | M7 — 60-second snapshot + operator pin + doctor freshness checks |
| Decision log | M8 — append-only JSONL with sha256-addressed entries; M8.3 worker writeback; M8.4 compaction |
| History | M3 read-only sync; M10 sync metadata records what happened across devices |
| Trust policy | M4/M5 worker auth chain; deliberately per-device, never synced |
| Handoff ledger | M4/M5 worker queue + audit transitions + bundle/MCP attribution |
| Artifact memory | docs/artifacts/M*/ evidence convention; M5.2b onward |
| Adapter portability | M9 — six-operation contract over shell, bundle, MCP, bridge |

When a future agent enters this project at, say, M14, they should be able to look at this list and understand which primitive a given commit was strengthening — and why a commit that doesn't strengthen any belongs in an adapter project, not this layer's core.

## Where the project is, where it's going

**Done (foundation):** M0–M5 — repo shape, doctor, sync, queue, OpenClaw bridge.

**Done (continuity arc):** M6–M10 — charter enforcement, context recovery, decision log, adapter pattern, multi-device sync.

**Next major (in roadmap):** M11 OSS Quickstart — make the project usable without OpenClaw. After M11, the project graduates from "an internal substrate" to "something a stranger can adopt."

**Beyond M11:** M12 packaging/release, M13 agent SDK / MCP surface formalization, M14 multi-project registry. These are extensions of the foundation, not redirection.

The trajectory is intentional. The substrate gets quieter as it gets more useful — fewer surprises, more invariants, less reason for the operator to think about it at all.

## See also

- [`../CHARTER.md`](../CHARTER.md) — the canonical product charter (continuity primitives, non-goals, milestone rule)
- [`roadmap.md`](roadmap.md) — milestone-by-milestone sequence
- [`handoff-vs-continuity.md`](handoff-vs-continuity.md) — why M4/M5 are a subsystem
- [`milestone-template.md`](milestone-template.md) — proposal shape every new milestone must answer
- [`m9-adapter-pattern.md`](m9-adapter-pattern.md) — the six-operation contract that makes adapters interchangeable
- [`walkthroughs/`](walkthroughs/) — pick the adapter you actually have and skip the rest
