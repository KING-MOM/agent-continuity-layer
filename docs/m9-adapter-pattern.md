# M9 Adapter Pattern Spec

Status: proposed
Scope: M9.0 through M9.4
Primary primitive: adapter portability
Secondary primitives: context recovery, decision log, handoff ledger, artifact memory, trust policy

## Gate Question

What is the minimum adapter contract that lets a web agent, local CLI agent, or OpenClaw/Mika recover context, read decisions, and write back outcomes without knowing this repo's internals?

Answer: six operations plus an identity shape. Everything else is transport.

| Operation | Direction | Authority | Returns | Existing source |
|---|---|---:|---|---|
| `whoami` | adapter -> layer | read-only | `AdapterIdentity` | M9-new |
| `read_context` | adapter -> layer | read-only | `ContextSnapshot` | M7 snapshot |
| `read_decisions(filter?)` | adapter -> layer | read-only | `DecisionEntry[]` | M8 log read |
| `append_decision(draft)` | adapter -> layer | write | `decision_id` | M8 decision append / worker writeback |
| `claim_task(filter?)` / `receive_task` | both directions | write | `WorkerTask | null` | M4 queue claim |
| `submit_result(task_id, result)` | adapter -> layer | write | `{status, appended_decision_ids[]}` | M4 submit + M8 writeback |

The contract specifies semantics, not transport. The same six operations can be served by shell scripts, OpenClaw's bridge, MCP tools, or operator-mediated JSON bundles.

## Design Decisions For M9

### Bundle Format

Use a single JSON object.

Web agents cannot reliably unpack tar or zip archives inside a chat surface. A single JSON object can be pasted, attached, inspected, signed later, and validated with one schema. Payloads stay JSON-native: context objects, decision entries, task records, and result drafts are embedded as objects or strings, not base64.

### Contract Authority

`docs/m9-adapter-pattern.md` is the canonical semantic spec for M9.0.

Schemas define the machine-checkable pieces, but the operation semantics live here until at least M9.2. A full machine-readable operation spec is deferred until the MCP implementation proves the need.

### Identity Attestation

Identity is descriptive in M9.

`AdapterIdentity` says what an adapter claims to be and which capabilities it supports. It is not cryptographic proof. Trust policy remains the enforcement layer for writes. Signed identities, bearer tokens, or local attestation belong in a future trust milestone.

### Existing Schemas

Reference existing schemas; do not redefine them.

M9 adds only adapter-specific schemas:

- `core/schemas/adapter-identity.schema.json`
- `core/schemas/adapter-bundle.schema.json`

The contract references these existing schemas for payloads:

- `core/schemas/context-snapshot.schema.json`
- `core/schemas/decision-entry.schema.json`
- `core/schemas/worker-task.schema.json`
- `core/schemas/worker-result.schema.json`

### Capabilities Vs Enforcement

Capabilities are declarative. Trust policy enforces.

The capability descriptor tells a host UI which operations to surface. It does not authorize writes. A web adapter may claim `append_decision: true`; the ingest path still validates the bundle and routes writes through the same decision log and worker submit code.

## Adapter Identity

`AdapterIdentity` is the adapter's public descriptor.

Minimum fields:

```json
{
  "schema_version": "1.0",
  "adapter_id": "claude-web-2026-05-25-operator",
  "adapter_type": "web-agent",
  "adapter": "claude",
  "display_name": "Claude web session",
  "transport": ["bundle"],
  "capabilities": {
    "whoami": true,
    "read_context": true,
    "read_decisions": true,
    "append_decision": "bundle-only",
    "claim_task": "bundle-only",
    "submit_result": "bundle-only"
  },
  "created_at": "2026-05-25T00:00:00Z"
}
```

Adapter type values for M9:

- `local-cli`
- `openclaw-bridge`
- `web-agent`
- `browser-extension`
- `read-only-auditor`
- `mcp-client`
- `other`

Adapter values for M9:

- `claude`
- `codex`
- `openclaw`
- `human`
- `chatgpt`
- `gemini`
- `grok`
- `kimi`
- `other`

`chatgpt`, `gemini`, `grok`, and `kimi` are web model brands. They are usually `bundle-only`: the operator exports a bundle, the model returns a bundle, and the operator ingests it locally.

Capability values for M9:

- `true`: operation can be performed directly by this adapter.
- `false`: operation is not supported.
- `bundle-only`: operation can be requested or returned through an operator-mediated bundle.

## Capability Matrix

| Adapter type | `whoami` | `read_context` | `read_decisions` | `append_decision` | `claim_task` | `submit_result` |
|---|---:|---:|---:|---:|---:|---:|
| Local CLI (`claude`, `codex`) | yes | yes | yes | yes | yes | yes |
| OpenClaw / Mika bridge | yes | yes | yes | yes | yes | yes |
| Web agent (`claude`, `chatgpt`, `gemini`, `grok`, `kimi`) | yes | yes | yes | bundle-only | bundle-only | bundle-only |
| Browser extension | yes | yes | yes | yes, if local API exists | yes, if local API exists | yes, if local API exists |
| Read-only auditor | yes | yes | yes | no | no | no |
| MCP client | yes | yes | yes | policy-gated | policy-gated | policy-gated |

## Operation Semantics

### `whoami`

Purpose: identify the adapter and declare capabilities.

Inputs: none, or a static local descriptor.

Returns: `AdapterIdentity`.

Failure modes:

- Invalid identity shape -> reject writes that depend on it.
- Unknown adapter type -> allow read-only only until trust policy says otherwise.

### `read_context`

Purpose: give a fresh agent the M7 context snapshot.

Inputs:

- Optional `format`: `json` or `markdown`.

Returns:

- JSON: `core/context-snapshot.json` shape.
- Markdown: `core/context-snapshot.md` content.

Failure modes:

- Missing snapshot -> surface doctor-style error with refresh hint.
- Stale snapshot -> return it with `stale: true`, do not hide it.

### `read_decisions(filter?)`

Purpose: expose durable why-memory.

Inputs:

- Optional `repo`.
- Optional `adapter`.
- Optional `limit`.
- Optional `since`.

Returns: newest-first decision entries from the M8 log.

Failure modes:

- Malformed log -> error, no partial best-effort read for write-capable adapters.
- Empty log -> return `[]`.

### `append_decision(draft)`

Purpose: let an adapter contribute durable reasoning without editing the log directly.

Inputs:

```json
{
  "decision": "Keep the bridge split for now.",
  "why": "Porting execution would consolidate code but does not strengthen a continuity primitive yet.",
  "refs": ["M5.5", "commit:334e01c"],
  "author": "operator"
}
```

The layer synthesizes:

- `schema_version`
- `id`
- `ts`
- `adapter`
- `repo`

Returns: `decision_id`.

Failure modes:

- Missing `decision` or `why` -> reject.
- Invalid adapter identity -> reject or require human-mediated bundle ingest.
- Trust policy refusal -> reject.

### `claim_task(filter?)` / `receive_task`

Purpose: hand a bounded task to a write-capable adapter.

Two equivalent patterns:

- Pull: adapter calls `claim_task`.
- Push/bundle: operator exports a bundle containing one task for the adapter to receive.

Inputs:

- Optional `kind`.
- Optional `repo`.
- Optional `trust_level`.
- Optional `adapter`.

Returns: `WorkerTask | null`.

Failure modes:

- Trust policy refusal -> no task returned.
- Adapter mismatch -> task remains unclaimed.
- Already claimed -> return null or race-lost response.

### `submit_result(task_id, result)`

Purpose: close the handoff loop and preserve artifacts/decisions.

Inputs:

- `task_id`
- `WorkerResult`

Returns:

```json
{
  "status": "completed",
  "appended_decision_ids": ["sha256:..."]
}
```

Failure modes:

- Wrong worker or adapter -> reject.
- Invalid `decisions` field -> reject entire submit.
- Missing expected artifacts -> route to awaiting approval.
- Failed task -> move to failed while preserving result/process metadata.

## Bundle Contract

Bundles make web agents viable. They are the same operations encoded as an operator-mediated request/response package.

### Import Bundle: Layer To Agent

Generated by the layer for a web or read-only adapter.

```json
{
  "schema_version": "1.0",
  "bundle_id": "bundle-...",
  "direction": "layer-to-adapter",
  "created_at": "2026-05-25T00:00:00Z",
  "for_adapter": { "adapter_id": "claude-web-..." },
  "allowed_operations": ["read_context", "read_decisions", "submit_result"],
  "context": {},
  "decisions": [],
  "task": null,
  "instructions": "Read the context and return an adapter-to-layer bundle."
}
```

### Export Bundle: Agent To Layer

Produced by a web agent and ingested by the operator.

```json
{
  "schema_version": "1.0",
  "bundle_id": "bundle-...",
  "direction": "adapter-to-layer",
  "created_at": "2026-05-25T00:00:00Z",
  "from_adapter": { "adapter_id": "claude-web-..." },
  "append_decisions": [],
  "submit_results": [],
  "notes": "Optional human-readable summary."
}
```

Ingest rules:

- Validate bundle schema first.
- Validate identity shape.
- Apply each requested write through existing code paths.
- Never let bundle ingest mutate source logs directly.
- Return an ingest report with applied operations, rejected operations, and decision/task ids.

## Existing Adapter Mapping

| Existing path | M9 operation mapping | Notes |
|---|---|---|
| `scripts/context.sh --json` | `read_context` | Local shell reference path. |
| `scripts/decisions.sh list/add` | `read_decisions`, `append_decision` | Existing M8 durable decision path. |
| `scripts/worker.sh claim/submit` | `claim_task`, `submit_result` | Existing M4/M8 handoff path. |
| `~/.openclaw/workspace/scripts/agent-worker.mjs` | bridge transport | OpenClaw/Mika bridge; shells out to this layer. |
| OpenClaw MCP `worker_*` tools | bridge/MCP-like surface | Existing verified path from M5.4. |
| Future `bundle.sh` | bundle transport | M9.1. |
| Future MCP tools | MCP transport | M9.2. |

## M9.0 - Contract And Schemas

Goal: define the adapter pattern without adding execution behavior.

Deliverables:

- `docs/m9-adapter-pattern.md` as the canonical semantic spec.
- `core/schemas/adapter-identity.schema.json`.
- `core/schemas/adapter-bundle.schema.json`.
- Memory inventory updated with adapter identity and adapter bundle entries.
- Existing adapters mapped in this document.

Acceptance:

- A fresh implementer can read the spec in under 10 minutes and name the six operations.
- Both new schemas parse and reject unknown fields.
- Memory inventory still covers all eight continuity primitives.
- Doctor's charter check remains OK.

Out of scope:

- No MCP tools.
- No bundle CLI.
- No authentication.
- No changes to worker execution.

Closing question: is the contract small enough that a fresh implementer can implement one read-only adapter without reading this repo's internals?

## M9.1 - Bundle CLI

Goal: make web-agent participation possible without shell access in the agent surface.

Deliverables:

- `scripts/bundle.sh export --for <adapter-id>`.
- `scripts/bundle.sh ingest <bundle.json>`.
- Export includes context, filtered decisions, optional task, allowed operations, and operator instructions.
- Ingest validates adapter bundle schema and routes writes through existing `decisions` and `worker` code paths.
- Evidence artifact showing a round trip with a fake web adapter.

Acceptance:

- Operator can export a read-only bundle for a web agent.
- Operator can ingest a bundle containing one valid decision draft.
- Operator can ingest a bundle containing one task result with optional decisions.
- Invalid bundle fails before any write.
- Source logs remain append-only; ingest never edits them directly.

Out of scope:

- No browser extension.
- No cryptographic signing.
- No multi-project bundle routing.
- No auto-upload to web chat surfaces.

Closing question: can the operator move a Claude-web or ChatGPT-web session through a real continuity workflow with two commands?

## M9.2 - MCP Tool Surface

Goal: translate the six operations into a standard tool surface.

Deliverables:

- MCP tool definitions for `whoami`, `read_context`, `read_decisions`, `append_decision`, `claim_task`, and `submit_result`.
- One reference manifest or extension directory.
- Request/response examples for each operation.
- Compatibility tests showing shell and MCP paths produce equivalent artifacts for read context, append decision, and submit result.

Acceptance:

- MCP `read_context` returns the same logical snapshot as `scripts/context.sh --json`.
- MCP `append_decision` writes through the same decision log path as `scripts/decisions.sh add`.
- MCP `submit_result` routes through the same worker submit semantics, including M8 decision writeback.
- Tool names and response shapes are stable enough for an adapter author to depend on.

Out of scope:

- No production auth model beyond existing local trust boundary.
- No hosted API.
- No replacement of the OpenClaw `.mjs` bridge unless the tool surface proves cleaner.

Closing question: does the six-operation contract map cleanly to MCP without inventing a second semantics layer?

## M9.3 - Doctor And Integrity Checks

Goal: make adapter portability visible and debuggable.

Deliverables:

- Doctor check for adapter identity schema validity where identities are installed or referenced.
- Doctor check for bundle schema validity in a configured bundle directory or explicit file path.
- Doctor check that `docs/m9-adapter-pattern.md` exists and references all six operations.
- Optional report of supported transports detected on the host: shell, bridge, MCP, bundle.

Acceptance:

- Malformed adapter identity produces a precise schema error.
- Malformed bundle produces a precise schema error.
- Missing optional bundle directory is INFO, not WARN.
- Missing M9 spec is WARN or ERROR depending on whether M9.0 has been marked complete.
- Doctor distinguishes unsupported transport from broken transport.

Out of scope:

- No network probing.
- No web-agent health checks.
- No validation of cryptographic identity.

Closing question: do stale or malformed adapter artifacts surface before an operator tries to use them?

## M9.4 - Walkthroughs And OSS Quickstart Bait

Goal: make the adapter pattern understandable to humans, not just schemas.

Deliverables:

- Walkthrough: ChatGPT web via bundle.
- Walkthrough: Claude web via bundle.
- Walkthrough: Codex local via shell.
- Walkthrough: Mika/OpenClaw via bridge.
- One minimal read-only auditor example.
- Troubleshooting table for common failures: stale context, rejected trust policy, malformed bundle, wrong adapter, missing task, invalid decisions.

Acceptance:

- A new user can complete at least one read-only workflow from the docs.
- A new user can understand how a write-capable workflow differs from a read-only workflow.
- The docs explicitly explain that bundles are operator-mediated and do not bypass trust policy.
- The docs point back to `CHARTER.md`, `docs/roadmap.md`, and this spec.

Out of scope:

- No polished website.
- No package release.
- No multi-project registry UX.
- No video demo.

Closing question: can a new user pick one adapter and one workflow without asking what this project is?

## Risks And Guardrails

- Do not let M9 become an auth project. Identity is descriptive until a trust milestone says otherwise.
- Do not let bundles bypass existing append/submit code paths.
- Do not duplicate existing schemas for context, decisions, tasks, or results.
- Do not make the VM or a web agent an executable-code authority.
- Keep transport-specific behavior below the six-operation semantic layer.

## M9 Completion Definition

M9 is complete when:

- The six-operation adapter contract is documented.
- At least one no-shell web workflow exists through bundles.
- At least one tool-style workflow exists through MCP or documented bridge mapping.
- Doctor can detect malformed adapter artifacts.
- A new user can follow one walkthrough and understand how continuity survives across the adapter boundary.
