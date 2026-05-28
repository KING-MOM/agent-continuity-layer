# agent-continuity-layer

Never lose the thread when switching agents, models, tools, machines, or sessions.

Most agent tools rediscover project state every session: chat history gets rolled forward or lost, RAG retrieves scattered fragments, and each new model reconstructs the "what happened?" story from scratch.

`agent-continuity-layer` compiles that state into explicit continuity artifacts. Fresh agents read context snapshots, decision logs, handoff ledgers, trust policy, project registry, and sync metadata instead of scattered history.

Those artifacts are attributed and audit-trailed. A future agent can see which adapter wrote a decision, which device last synced memory, which task produced an artifact, and which trust boundary allowed the write.

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

The substrate keeps that state in explicit files and schemas instead of buried chat history. RAG rediscovers; continuity compiles.

## Durability Model

The decision log is append-only JSONL. Each decision has a deterministic sha256 content ID, so duplicate writes collapse cleanly and future records can refer to `decision:<id>`. Worker tasks keep an audit trail for every state transition. Context snapshots are derived and regeneratable; the operator-maintained pin is the small piece of judgment that travels between sessions.

Starting with v0.2.0, release artifacts are signed with cosign keyless OIDC (sigstore) — the install path verifies signatures before extracting. Adapter identities remain descriptive, not cryptographic, in v0.x; that's a future trust milestone.

## Install

**Prerequisite (v0.2.0+):** install [cosign](https://github.com/sigstore/cosign) for release signature verification:

```bash
brew install cosign   # macOS; see sigstore.dev/install for linux
```

The install scripts verify each release artifact against this repo's release-workflow OIDC identity before extracting. v0.1.x releases were unsigned and bootstrap auto-skips verification on those tags.

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

Either path: bootstrap resolves the latest release, downloads tarball + integrity/signature sidecars, verifies them, extracts to a tempdir, and runs `install.sh`. Install itself writes only under `$XDG_DATA_HOME/agent-continuity/` and `$HOME/.local/bin/agent-continuity`; the `connect` step is the one that touches third-party app configs and is why it's separate by default.

After install:

```bash
agent-continuity --version
agent-continuity doctor --human
```

If you'd rather see and verify everything before any code runs, the step-by-step tarball flow lives in [`docs/install.md`](docs/install.md). Integrity guarantee is identical either way.

**Using Claude Code, Codex CLI, or another shell-capable agent?** Tell it to install agent-continuity-layer from `https://github.com/KING-MOM/agent-continuity-layer` and it can run the one-liner for you.

The checksum detects transport corruption. The cosign signature verifies that the artifact was produced by this repo's release workflow identity. The bootstrap script itself is still fetched over HTTPS, so users who need maximum supply-chain assurance should use the step-by-step install flow in [`docs/install.md`](docs/install.md) and verify the downloaded bootstrap/release assets before executing anything.

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

agent-continuity ships a JSON-RPC 2.0 MCP server over stdio:

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

Trust-policy behavior is documented in [`docs/trust-policy.md`](docs/trust-policy.md). The short version: v0.x assumes a single operator, local per-device policy, descriptive adapter identity, and explicit audit. Multi-tenant or cross-org authority is future work.

## Related Thinking

Andrej Karpathy's [`LLM Wiki`](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) describes the same deeper pattern for personal and research knowledge bases: don't make the model rediscover knowledge from raw documents every time; maintain a persistent, compounding artifact that gets updated as work happens.

`agent-continuity-layer` applies that pattern to multi-agent work. The compiled artifact is not a single-author wiki; it is a set of shared memory primitives with attribution, audit, trust gates, device sync, and adapter portability. That extra machinery matters because many agents, tools, devices, and transports can write to the same continuity layer.

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

Current release: `v0.2.0`

Implemented major arcs:

- M6: charter enforcement + memory inventory
- M7: project context recovery
- M8: cross-agent decision writeback
- M9: adapter portability through shell, bundle, MCP, and OpenClaw bridge
- M10: fake-VM multi-device sync for decisions, registry, and context pin; real-VM verification parked until available
- M11: OSS quickstart
- M12: packaging / release baseline
- M13: MCP stdio server + integration docs + Python SDK + reference agent
- M14: multi-project registry CLI, auto-registration, and doctor health check
- M15: reproducible builds, SBOM, signed release artifacts
- M16: device-to-device handoff export/import/inspect

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
