# agent-continuity-layer

Never lose the thread when switching agents, models, tools, machines, or sessions.

`agent-continuity-layer` is a memory substrate for AI-agent work. It preserves context, decisions, handoffs, and artifacts across agents, models, tools, machines, and sessions.

Delegation is a mechanism. Continuity is the product.

The core rule is intentionally narrow: **sync memory, not behavior**. Git owns code, schemas, docs, migrations, and scripts. Local devices own trust policy and adapter config. The VM/backend, when used, carries memory only.

The public contract is six operations:

```text
whoami
read_context
read_decisions
append_decision
claim_task
submit_result
```

Those operations are exposed through shell, MCP stdio, web bundles, the Python SDK, and the OpenClaw/Mika bridge. Web bundles support Claude, ChatGPT, Gemini, Grok, and Kimi as explicit adapter brands. The repo grew out of production Example Agents workflows, but OpenClaw is one adapter among many, not the center of the project.

## What It Does

A fresh agent should be able to enter a project and know:

- what this project is,
- what happened recently,
- why key decisions were made,
- what work is in flight,
- what it is allowed to do,
- and what artifact it should leave for the next agent.

The substrate keeps that state in explicit files and schemas instead of buried chat history.

## Durability Model

The decision log is append-only JSONL. Each decision has a deterministic sha256 content ID, so duplicate writes collapse cleanly and future records can refer to `decision:<id>`. Worker tasks keep an audit trail for every state transition. Context snapshots are derived and regeneratable; the operator-maintained pin is the small piece of judgment that travels between sessions.

Current limit: release checksums detect transport corruption, not publisher identity; adapter identities are descriptive, not cryptographic signatures. Signed releases and cryptographic identity are future trust milestones.

## Install

**One command, install + wire to all your local agents:**

```bash
curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh | bash -s -- --connect-all
```

`--connect-all` opts you in to a second step that writes MCP-server entries into Claude Desktop, Cursor, and Zed configs (with backups) and installs thin skills into Claude/Codex/OpenClaw homes when they exist. Restart any running MCP clients afterward.

**Default (install only, conservative — wiring stays explicit):**

```bash
curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh | bash
agent-continuity connect all --apply
```

Either path: bootstrap resolves the latest release, downloads tarball + `.sha256`, verifies with `shasum -a 256 -c`, extracts to a tempdir, runs `install.sh`. Install itself writes only under `$XDG_DATA_HOME/agent-continuity/` and `$HOME/.local/bin/agent-continuity`; the `connect` step is the one that touches third-party app configs and is why it's separate by default.

After install:

```bash
agent-continuity --version
agent-continuity doctor --human
```

If you'd rather see and verify everything before any code runs, the step-by-step tarball flow lives in [`docs/install.md`](docs/install.md). Integrity guarantee is identical either way.

**Using Claude Code, Codex CLI, or another shell-capable agent?** Tell it to install agent-continuity-layer from `https://github.com/KING-MOM/agent-continuity-layer` and it can run the one-liner for you.

The checksum detects transport corruption — not publisher identity. An attacker who can rewrite the tarball can rewrite the bootstrap and its sha256 too. Signed releases are a future trust milestone; the same honest framing applies to either install path.

## Connect Everything

Install makes the substrate available. `connect` points local adapter hosts at it:

```bash
agent-continuity connect doctor
agent-continuity connect all --apply
agent-continuity connect doctor
```

This configures Claude Desktop, Cursor, and Zed to launch `agent-continuity mcp serve`, installs thin skills for Claude/Codex/OpenClaw homes when present, and reports OpenClaw bridge status. Dry-run is the default; `--apply` is required to write, and existing config files are backed up first.

You can also connect one host at a time:

```bash
agent-continuity connect cursor --apply
agent-continuity connect codex --apply
```

More detail: [`docs/connect.md`](docs/connect.md)

## Quickstart

No VM. No OpenClaw. No API key. No real LLM. The quickstart runs in a sandbox and leaves your real continuity state untouched.

```bash
agent-continuity quickstart init
agent-continuity quickstart doctor
agent-continuity quickstart enqueue
agent-continuity quickstart run-fake-worker
agent-continuity quickstart decisions list
```

The final command shows the thing this project exists to preserve: a durable, attributed decision from a delegated worker, tied back to the task that produced it.

When done:

```bash
agent-continuity quickstart reset
```

More detail: [`docs/quickstart.md`](docs/quickstart.md)

## MCP

`v0.1.5` includes the same JSON-RPC 2.0 MCP server over stdio, plus sanitized public examples and release artifacts:

```bash
agent-continuity mcp serve
```

It exposes the six adapter-contract operations:

- `whoami`
- `read_context`
- `read_decisions`
- `append_decision`
- `claim_task`
- `submit_result`

Manual smoke:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize"}' | agent-continuity mcp serve
```

Client setup snippets for Claude Desktop, Cursor, Zed, and MCP Inspector: [`docs/mcp-integration.md`](docs/mcp-integration.md)

## Python SDK

For local orchestrators and reference agents that can run Python, M13.2 adds a thin SDK over the same six operations:

```python
from agent_continuity import Substrate

substrate = Substrate()
context = substrate.read_context()
decision_id = substrate.append_decision(
    adapter="human",
    repo="my-project",
    decision="Record the load-bearing choice.",
    why="Future agents need the reason, not just the resulting diff.",
)
```

The SDK is not a second implementation. It shells out to `agent-continuity mcp tool ...`, so writes still go through the canonical decision, queue, and trust paths.

More detail: [`docs/python-sdk.md`](docs/python-sdk.md)

## Reference Agent

M13.3 includes a no-LLM reference agent that uses the SDK shape end-to-end:

```bash
agent-continuity reference-agent --dry-run
agent-continuity reference-agent
agent-continuity decisions list --adapter codex --limit 1
```

It performs `read_context → decide → append_decision`, then leaves a durable decision in the canonical log. The `author` field is `reference-agent-demo` so readers can distinguish it from a real Codex CLI run.

More detail: [`docs/reference-agent.md`](docs/reference-agent.md)

## Core Ideas

| Primitive | Purpose |
|---|---|
| Context recovery | Let a fresh agent become useful in under 60 seconds. |
| Decision log | Preserve why choices were made, not just what changed. |
| Trust policy | Define what each adapter may do without re-asking. |
| Handoff ledger | Track delegated work from enqueue to result. |
| Artifact memory | Keep patches, reports, receipts, screenshots, and summaries. |
| Adapter portability | Let shell, MCP, bundles, OpenClaw, and future hosts use the same continuity contract. |
| Project registry | Identify projects across devices without confusing local paths for global truth. |
| History / sync | Make memory portable without making the VM a code or config authority. |

See [`docs/north-star.md`](docs/north-star.md) for the product direction and [`CHARTER.md`](CHARTER.md) for the rules that keep the project from drifting.

Trust-policy behavior is documented in [`docs/trust-policy.md`](docs/trust-policy.md). The short version: v0.1.x assumes a single operator, local per-device policy, descriptive adapter identity, and explicit audit. Multi-tenant or cross-org authority is future work.

## Command Surface

After install, `agent-continuity` is a thin dispatcher over the same scripts used in the repo:

```bash
agent-continuity doctor --human
agent-continuity connect doctor
agent-continuity context --json
agent-continuity decisions list
agent-continuity quickstart init
agent-continuity worker list
agent-continuity bundle export --for-adapter chatgpt-web-demo
agent-continuity mcp serve
agent-continuity reference-agent --dry-run
agent-continuity migrate --dry-run
```

The dispatcher does not reimplement behavior; it routes to the canonical script for each operation.

## Architecture

```text
Git repo                 owns code, schemas, docs, migrations
Host config/state/cache  owns local trust, decisions, queues, sync metadata
VM / remote backend      stores memory only, never executable behavior
Adapters                 shell, MCP, bundle, OpenClaw, Claude, Codex, future hosts
```

Important boundary: **sync memory, not behavior**. Trust policy, executable scripts, skills, and adapter configuration are not silently synced from the VM.

## Repository Layout

```text
bin/                    installed CLI dispatcher
agent_continuity/       Python SDK wrapper
core/                   schemas, inventory, version, migrations, context files
core/mcp/               MCP tool manifest
scripts/                canonical operational scripts
docs/                   north star, quickstart, MCP integration, roadmap, architecture
fixtures/               quickstart fixture project
skills/                 thin agent instructions
adapters/               adapter notes / bridge contracts
v0.1-reference/         sanitized v0.1 provenance, preserved for historical context
```

## Status

Current release: `v0.1.5`

Implemented major arcs:

- M6: charter enforcement + memory inventory
- M7: project context recovery
- M8: cross-agent decision writeback
- M9: adapter portability through shell, bundle, MCP, and OpenClaw bridge
- M10: fake-VM multi-device sync for decisions, registry, and context pin; real-VM verification parked until available
- M11: OSS quickstart
- M12: packaging / release baseline
- M13: MCP stdio server + integration docs + Python SDK + reference agent

Roadmap: [`docs/roadmap.md`](docs/roadmap.md)

## What This Is Not

- Not just an OpenClaw plugin.
- Not just a worker queue.
- Not an autonomous-agent runner.
- Not a VM-controlled config system.
- Not a broad write-authority grant for agents.

If a change improves execution but not continuity, trust, handoff, artifacts, or adapter portability, it probably belongs in an adapter project, not this layer.

## Docs

- [`docs/north-star.md`](docs/north-star.md) — two-minute product direction
- [`docs/connect.md`](docs/connect.md) — connect local adapters in one step
- [`docs/handoff.md`](docs/handoff.md) — device-to-device state handoff (export / import / inspect)
- [`docs/quickstart.md`](docs/quickstart.md) — first successful delegated task
- [`docs/mcp-integration.md`](docs/mcp-integration.md) — MCP client setup
- [`docs/python-sdk.md`](docs/python-sdk.md) — Python wrapper for the adapter contract
- [`docs/reference-agent.md`](docs/reference-agent.md) — no-LLM SDK demo agent
- [`docs/trust-policy.md`](docs/trust-policy.md) — local trust model and enforcement points
- [`docs/m9-adapter-pattern.md`](docs/m9-adapter-pattern.md) — six-operation adapter contract
- [`docs/handoff-vs-continuity.md`](docs/handoff-vs-continuity.md) — why the worker queue is a subsystem
- [`docs/roadmap.md`](docs/roadmap.md) — milestone sequence
- [`docs/install.md`](docs/install.md) — lower-level install notes
- [`docs/versioning.md`](docs/versioning.md) — SemVer commitment, API surface, deprecation policy, criteria for v1.0
- [`SECURITY.md`](SECURITY.md) — vulnerability disclosure process and response expectations
