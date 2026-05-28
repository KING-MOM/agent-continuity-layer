# Trust Policy

The trust policy answers one question: what can a local adapter do without re-asking the operator?

In v0.1.2 this is deliberately a **single-operator, per-device** trust model. Identity is descriptive, not cryptographic. The policy is a local file; the VM never becomes a config authority.

## Where It Lives

```text
$XDG_CONFIG_HOME/agent-continuity/trust-policy.json
# default: ~/.config/agent-continuity/trust-policy.json
```

The schema is [`../core/schemas/trust-policy.schema.json`](../core/schemas/trust-policy.schema.json). A starter example lives at [`../core/schemas/trust-policy.example.json`](../core/schemas/trust-policy.example.json).

## What It Controls

The policy has two related surfaces:

| Surface | Purpose |
|---|---|
| `default` / `repos[]` | Canonical policy routing for this layer. Enqueue resolves task kind, target adapter, trust level, and repo against this policy. |
| `grants[]` | Time-limited bridge grants compatible with the M5 OpenClaw `.mjs` model. `worker.sh trust-check` reports whether a task is auto-approvable under these grants. |

Both exist on purpose. The `repos[]` path decides whether a task can enter the queue normally. The `grants[]` path preserves the OpenClaw bridge vocabulary while the bridge remains a peer adapter.

## Enforcement Points

| Operation | Enforcement |
|---|---|
| `worker enqueue` | Checks repo policy: kind allow/deny, target adapter, trust-level ceiling, human-approval requirements, dangerous permissions. |
| `worker claim` | Re-checks dangerous permissions and adapter/worker ownership before moving a task from queued to claimed. |
| `worker start` | Re-checks dangerous permissions and claimed ownership before moving claimed to running. |
| `worker submit` | Requires the owning worker identity and validates result shape; worker-result decisions flow into the canonical decision log. |
| `bundle ingest` | Validates bundle schema, adapter identity shape, task hash, and then routes through the same claim/submit paths. |
| `mcp tool claim_task` / `submit_result` | Routes through `worker.sh`; MCP adds transport attribution (`mcp:<adapter_id>`) but not separate policy. |

Direct decision append (`decisions.sh add`, `Substrate.append_decision`, `mcp append_decision`) is not a broad execution grant. It is append-only, schema-validated, adapter-enum-gated, and attributed. It does not edit code, trust policy, or queue state.

## Identity Model

Adapter identity in v0.1.2 is descriptive:

- `claude`
- `codex`
- `openclaw`
- `human`

Those tokens appear in schemas so records are filterable and attributable. They are not signatures. A local process that can run the CLI under your user account is assumed to be acting inside your operator boundary. Multi-user, cross-org, cryptographic identity is future work.

## Safe Defaults

A fresh bootstrap policy is conservative:

- default `allow_kinds` is empty,
- dangerous bypass is refused,
- network permissions other than `off` are refused in task permissions,
- broad trust levels require explicit operator approval,
- quickstart grants are sandboxed and time-limited.

The quickstart writes its own isolated trust policy under the `agent-continuity-quickstart` XDG namespace. It does not mutate your real trust policy.

## What Does Not Sync

Trust policy is **never** synced by M10. Device A's trust grants say nothing about Device B. Each device decides what it allows.

The VM sync surface carries memory only: decisions, project registry entries, and context pin. It does not carry executable scripts, skills, adapter config, or trust policy.

## Inspecting Policy

```bash
agent-continuity doctor --human
agent-continuity worker trust-list
agent-continuity worker trust-check <task-id>
```

`doctor` reports whether the policy is present and whether grants are expired. `trust-check` explains why a specific task is or is not covered by grants.

## Current Limits

- Single-operator assumption.
- Descriptive identity only; no signatures.
- No multi-tenant policy isolation.
- Direct decision append is append-only and attributed, but not separately trust-policy gated.
- OpenClaw/Mika has its own daemon-level trust outside this repo.

These are design limits for v0.1.x, not hidden guarantees. If a future release changes them, it should be a trust milestone and likely a semver-significant release.
