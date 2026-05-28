# Architecture

## One-sentence statement

Agent Continuity Layer preserves project memory, decisions, trust, and handoffs across agents, sessions, tools, devices, and model providers. OpenClaw is one control plane; Claude and Codex are two workers; the VM is one optional memory backend.


## Charter

The canonical product charter is [`../CHARTER.md`](../CHARTER.md). Architecture decisions should preserve the hierarchy: continuity first, then trust, handoff, delegation, and adapters.

For why M4/M5 don't make this project a worker queue, see [`handoff-vs-continuity.md`](handoff-vs-continuity.md).

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│ Humans (operators)                                           │
└──────────────────────────────────────────────────────────────┘
                          │ approvals
                          ▼
┌──────────────────────────────────────────────────────────────┐
│ OpenClaw (control plane)                                     │
│   - Mika (social interface)                                  │
│   - Channel ingress: messaging, Telegram, web                │
│   - Routing + trust policy resolution                        │
│   - Worker task queue                                        │
└──────────────────────────────────────────────────────────────┘
            │ worker-task                       ▲ results
            ▼                                   │
┌─────────────────────────┐         ┌─────────────────────────┐
│ Claude (worker)         │         │ Codex (worker)          │
│   - reasoning           │         │   - repo execution      │
│   - review              │         │   - tests / builds      │
│   - artifacts           │         │   - mechanical edits    │
└─────────────────────────┘         └─────────────────────────┘
            │                                   │
            └──────────┬────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────────┐
│ VM (shared memory only — NOT config authority)               │
│   ~/life-agents/sessions/{uuid}/context.md                   │
│   ~/life-agents/sessions/{uuid}/decisions.md                 │
│   ~/life-agents/sessions/{uuid}/history.md                   │
│   ~/life-agents/sessions/registry.json                       │
└──────────────────────────────────────────────────────────────┘
                       ▲
                       │ executable code, schemas, docs, migrations
┌──────────────────────────────────────────────────────────────┐
│ Git (source of truth)                                        │
│   this repo: agent-continuity-layer                          │
└──────────────────────────────────────────────────────────────┘
```

## Trust boundaries

Three boundaries matter:

1. **Channel → OpenClaw.** Inbound messages are untrusted until policy classifies them. Mika handles this today.
2. **OpenClaw → worker.** Crossed only by a validated `worker-task`. The task carries the resolved `trust_level` — the worker re-checks it on claim.
3. **Worker → repo / artifact.** Worker honors `files_allowed`, `branch`, and the repo's `files_denied`. Anything outside requires `needs_human_approval: true` on the result.

## What v0.1 conflated

v0.1's single skill ([`v0.1-reference/life-agents-unified/SKILL.md`](../v0.1-reference/life-agents-unified/SKILL.md)) mixed:

- VM transport (rsync, ssh)
- Skill distribution (auto-syncing `~/.claude/skills/`)
- Project recognition (fuzzy match)
- Context loading
- Account-device binding (security check)
- Audit logging

v0.2 splits these by trust level:
- **VM transport + project recognition + context loading** → adapters (per-platform, swappable)
- **Skill distribution** → Git (this repo). The VM no longer ships executable code.
- **Account-device binding + trust policy** → `core/schemas/trust-policy.schema.json` + every adapter validates
- **Audit** → built into `worker-task.audit.transitions`, plus the existing `~/life-agents/security.log`

## Stub inventory

Everything below is a stub. Each carries a brief in its file pointing at what implementation should look like.

| Stub | Lives in | Replaces |
|---|---|---|
| `scripts/migrate.sh` | this repo | (new in v0.2) |

`scripts/doctor.sh` shipped in M1. Read-only, never mutates.
`scripts/install-thin-skills.sh` shipped in M2. Dry-run by default, backup-before-overwrite, atomic writes.
`scripts/sync.sh` shipped in M3. Read-only pull of context/decisions/history from VM into `~/.cache/agent-continuity/`. Never writes to VM, never touches `~/.claude/skills/`. Clean no-op when `life-agents.json` is absent.
`scripts/worker.sh` shipped in M4.0. Subcommand surface (`enqueue` / `list` / `show` / `claim` / `submit`) over a per-task-file queue at `~/.cache/agent-continuity/queue/{state}/`. Policy enforced at enqueue AND re-checked at claim. Audit trail per task. Supersedes the original `worker-enqueue.sh` stub.
| OpenClaw daemon wiring | `adapters/openclaw/` | OpenClaw orchestrator code currently in `~/.openclaw/` |
| Claude worker entry | `adapters/claude/` | (new in v0.2) |
| Codex worker entry | `adapters/codex/` | (new in v0.2) |
| `skills/*/SKILL.md` | this repo | Replaces v0.1's monolithic skill — split per host |

## Migration

v0.1 → v0.2 is non-destructive. The VM file layout (`~/life-agents/sessions/*`) and the local `~/.claude/life-agents.json` are reused as-is. What changes:

1. Skills stop auto-syncing from the VM. New skills install from Git into `~/.claude/skills/agent-continuity-claude/` etc.
2. Worker tasks become explicit. Today OpenClaw invokes Claude/Codex implicitly; in v0.2 every cross-agent invocation produces a `worker-task` row.
3. Trust policy becomes explicit. Today it's hard-coded in OpenClaw / Mika; in v0.2 it's a versioned file per device.

`scripts/migrate.sh` will perform the local-side changes when implemented.

## Implementation milestones

1. **M0 (this scaffold)** — repo structure, schemas, stubs. Done.
2. **M1 (doctor)** — `scripts/doctor.sh` works. Read-only health report: repo state, agent homes, installed thin skills (with drift detection via sha256 + version), VM config + reachability (no `known_hosts` mutation), worker bridge (canonical source + installed-extension path), trust-policy validation + expiry, v0.1 residue scan. JSON-first output; non-zero exit on warnings/errors. Done.
3. **M2 (install-thin-skills)** — `scripts/install-thin-skills.sh` installs each agent's SKILL.md to its canonical target (`~/.claude/skills/agent-continuity/`, `~/.codex/skills/agent-continuity/`, `~/.openclaw/workspace/skills/agent-continuity/`). Dry-run by default, `--apply` writes, atomic writes via tmp+rename, backup-before-overwrite, `--force` only for downgrades (target newer than source). Refuses to create agent homes that don't exist. SKILL.md frontmatter now carries `version`, enabling installed_older / installed_newer detection. Done.
4. **M3 (memory sync read-only)** — `scripts/sync.sh` pulls every project's context (`context.md` + `decisions.md` + `history.md`) from the VM into `~/.cache/agent-continuity/{project_uuid}/`. SSH-cat per file, atomic writes (tmp + rename). **Host-key pinned** against a dedicated `~/.config/agent-continuity/known_hosts` (strict checking, `GlobalKnownHostsFile=/dev/null`); sync refuses to pull data until the operator runs `sync.sh --trust-host` and confirms the fingerprint with `--confirm-fingerprint SHA256:...`. This closes the MITM/context-poisoning gap an unpinned probe would have left open. Doctor uses the same known_hosts file and reports `reachability: unpinned` when the operator hasn't bootstrapped yet, `key_mismatch` (ERROR) if the server's key differs from the pin. Registry validated against `core/schemas/project-registry.schema.json` v1 (lightweight stdlib validator). Markdown files checked non-empty only. `--list` shows projects without pulling **and without mutating the local cache** (P3 fix). `--project UUID` limits scope. Clean no-op (exit 0) when no `life-agents.json` present. Per-file `.meta.json` sidecar records `synced_at`, `source_vm`, sha256s. Done.
5. **M4 (worker happy path + trust)** — broken into substeps:
   - **M4.0 (foundation)** — `scripts/worker.sh` queue with subcommands enqueue/list/show/claim/submit. Policy enforced at enqueue AND re-checked at claim (defense in depth — policy may tighten between the two). Audit trail per task in `audit.transitions`. Per-task files under `~/.cache/agent-continuity/queue/{state}/`. Done.
   - **M4.1 (ownership + atomicity)** — claim uses `os.rename(queued/T.json → claimed/T.json.claiming-{pid}-{rand})` as the exclusion primitive (POSIX-atomic; loser sees `FileNotFoundError`). `--adapter` required on claim and verified against `task.target.adapter` (no cross-adapter claims). Submit refuses unless `args.worker == task.claimed_by`. `_write_task_to_state` returns `(path, error)` so `PermissionError`/`OSError` produce structured exit-1 reports rather than crashing. `running` removed from the schema status enum. Done.
   - **M4.2 (approval flow + artifacts validation)** — `approve` and `reject` subcommands handle `awaiting-approval` transitions. Pre-claim approve → `queued` (worker can claim); pre-claim reject → `cancelled` (no work done). Post-submit approve → `completed`; post-submit reject → `rejected`. Distinguished by presence of `result`. Submit auto-routes to `awaiting-approval` if `expected_artifacts` declared on the task aren't fulfilled by `result.artifacts` — surfaces missing items via `result.missing_expected_artifacts` and `result.approval_reason` for the human approver. Done.
   - **M4.3 (fixture + first grant)** — fresh throwaway repo at `~/.openclaw/workspace/m4-fixture/` (own git history; not a submodule, not pushed). One README + one `fixtures/sample.md` for tasks to edit. First repo grant added to `~/.config/agent-continuity/trust-policy.json` (host-side, not in this repo): origin `file:///Users/<operator>/.openclaw/workspace/m4-fixture`, `max_trust_level: scoped-write`, `allow_kinds: [code-change, code-review, test-run]`, `require_human_approval_for: [code-change, elevated]` (so the first real Codex run hits an explicit operator approval gate before any byte changes), `allowed_workers: [codex, claude]`, files_denied = secrets defaults, `expires_at: 2026-06-01T23:59:59Z` (7-day). Lifecycle verified: a `code-change` task targeting the fixture origin lands in `awaiting-approval` with reason "matched repo grant"; the same task without `--repo` falls back to the default policy and is rejected. Done.
   - **M4.4 (Codex first task)** — first real cross-vendor agent orchestration. OpenAI Codex (running in a separate session, not impersonated) claimed `task-8d267ed68469` with `--adapter codex --worker codex-on-operator-device`, appended one dated line to `fixtures/sample.md` (working-tree only, no commit), and submitted a unified-diff patch artifact that satisfied the task's `expected_artifacts` constraint. Full lifecycle in audit: `enqueue:operator-via-claude → approve:operator → claim:codex-on-operator-device → complete:codex-on-operator-device`. `files_allowed: ["fixtures/sample.md"]` was honored (git status dirty only on that file). Done.
   - **M4.5 (Claude second task)** — Claude (this session, `--adapter claude --worker claude-on-operator-device`) reviewed task-8d267ed68469's still-uncommitted patch read-only and produced a compliance report at `review-of-task-8d267ed68469.md`. Audit chain: `enqueue:operator-via-claude → claim:claude-on-operator-device → complete:claude-on-operator-device` (3 transitions — no approval gate because `code-review` isn't in `require_human_approval_for`). Read-only verified: fixture working tree unchanged after the review (still dirty only on M4.4's edit). Result artifact (1503-char markdown) matched `expected_artifacts`. Done.
   - **M4.6 (Mika/OpenClaw integration)** — reference Python client at [`adapters/openclaw/queue_client.py`](../adapters/openclaw/queue_client.py): subprocess-driven wrapper over `scripts/worker.sh --json` with typed signatures (`Kind`, `TrustLevel`, `WorkerAdapter`, `TaskState`, `ExpectedArtifact`), exception hierarchy (`WorkerError`, `PolicyError` for rc=2, `UsageError` for rc=64), and the operator-facing surface only (`enqueue`, `list_tasks`, `show`, `approve`, `reject` — `claim`/`submit` deliberately not exposed because OpenClaw is not a worker). The full OpenClaw daemon wire-up (Mika decides → calls `queue_client.enqueue` → operator approves via Mika UI → worker picks up) lives in the OpenClaw repo, not here. This repo's M4.6 deliverable is the stable Python contract OpenClaw integrators import. Verified end-to-end via import: research/read-only enqueues to queued, code-change/scoped-write against fixture enqueues to awaiting-approval, no-grant code-change raises `PolicyError`, approve transitions pre-claim awaiting-approval → queued, list+show round-trip works. Done.
6. **M5 (controlled writeback + migration)** — Workers may append to `decisions.md` (with task-ID attribution) and rewrite `context.md` under stricter approval. `scripts/migrate.sh` translates a live v0.1 install to v0.2 without losing context.

## Open questions

Tracked in this doc rather than scattered TODOs:

- **Queue transport?** Filesystem (jsonl on the VM), Redis, or NATS? Lean filesystem-on-VM for v0.2 to match v0.1's transport assumptions.
- **Adapter authentication to the queue?** Reuse SSH keys per v0.1, or introduce per-adapter tokens? Default: reuse SSH for v0.2, revisit at M3.
- **What lives in OpenClaw vs in this repo?** Today: routing, channel ingress, Mika herself live in OpenClaw. The continuity-layer adapter is a *client* of OpenClaw, not a replacement. If that line moves, document the move in `decisions.md` at the repo level.
