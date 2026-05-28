# M5 Integration Plan — Bridge `agent-worker.mjs`, preserve replacement option

> **Status:** plan only. No code changes yet. Direction is **Bridge first, Replace later if bridge proves cleaner.** Awaiting operator sign-off before M5.1 starts.
>
> **Revision history:** previously framed as "Replace `agent-worker.mjs`" (commit `955a01d`). Operator corrected the direction to Bridge; this rewrite reflects that. Replace remains an explicit deferred option, not the active plan.

## Why this exists

During M4.6 discovery, I found that OpenClaw already shipped `agent-worker.mjs` (674 lines, Node.js) on 2026-05-22 — the day before this repo started. It implements a worker-task queue that overlaps substantially with what `agent-continuity-layer` built across M0–M4. The collision was real; the question was how to resolve it.

**The chosen direction: Bridge (C).** Keep `agent-worker.mjs` as the execution engine; route its queue/trust/audit operations through this layer so this layer becomes the canonical state. Defer the "port execution and retire `.mjs`" decision until the bridge is in production and we can compare ergonomics in practice rather than on paper.

This is materially smaller scope than Replace:

| | Replace (deferred) | Bridge (M5 active) |
|---|---|---|
| Codex/Claude subprocess execution | port to Python in this layer | stay in `.mjs` |
| Prompt construction | port verbatim | stay in `.mjs` |
| Timeout / process management | port | stay in `.mjs` |
| Result schema + normalization | port | stay in `.mjs` |
| Queue state files (`pending/done/failed/...`) | here | here |
| Trust grants | here | here |
| Audit trail | here | here |
| MCP tool surface (`worker_enqueue` etc.) | rewritten to point here | rewritten to point here |
| `.mjs` deprecation | yes | no |

Blast radius drops from "rebuild every capability `.mjs` has" to "make `.mjs` a thin client of this layer for the data plane, leave the execution plane alone."

## What `agent-worker.mjs` does (full inventory kept for reference)

| Capability | Where in `.mjs` | M5 disposition |
|---|---|---|
| Queue directory layout (`pending/running/done/failed/logs/schemas/`) | `ensureDirs()` | **Bridge:** queue files move to `~/.cache/agent-continuity/queue/`; `.mjs` reads/writes via shell-out to `worker.sh` |
| `doctor` (codex/claude version probes) | — | keep in `.mjs` |
| `enqueue` (build task JSON, write to `pending/`) | `buildTask()` | **Bridge (M5.2):** wraps `worker.sh enqueue` |
| `list` / `show` | scan dirs / read one task | **Bridge (M5.2):** wraps `worker.sh list` / `show` |
| `run-next` / `run <id>` (spawn codex/claude, capture, validate) | `runTask()` + `runProcess()` + `codexArgs()` / `claudeArgs()` | **Stay in `.mjs` (M5.3):** reads next task via `worker.sh list --state=queued`, claims via `worker.sh claim --adapter codex --worker codex-on-operator`, executes locally, submits via `worker.sh submit` |
| `trust-list` / `trust-add` / `trust-check` / `trust-remove` | manage `worker-tasks/trust-policy.json` | **Bridge (M5.2):** wraps `worker.sh trust-*` (added in M5.2) |
| Repo-path allowlist (`assertInsideHomeOrWorkspace`) | inside `enqueue` | **Bridge:** moves to this layer (since `enqueue` does); same 4 roots |
| Codex invocation (`exec --ephemeral --sandbox ... --cd ... --output-schema ... --output-last-message ...`) | `codexArgs()` | stay in `.mjs` |
| Claude invocation (`-p --output-format json --json-schema ... --permission-mode plan --tools Read,Grep,Glob --no-session-persistence`) | `claudeArgs()` | stay in `.mjs` |
| Prompt construction (verbatim task JSON + 8 operating rules) | `buildPrompt()` | stay in `.mjs` |
| Process management (SIGTERM → SIGKILL +5s, NO_COLOR, cwd=repo, stdio[0]=ignore) | `runProcess()` | stay in `.mjs` |
| Log files (`logs/<id>.log`) | combined stdout/stderr | stay in `.mjs` (writes to this layer's queue dir) |
| Result schema (`worker-result.schema.json`) | 10 required fields | port to this layer (M5.1) so the queue can validate inbound results from `.mjs`'s submit |
| Result normalization (status enum, severity, Claude fallback chain) | `extractClaudeResult`, `normalizeWorkerResult` | stay in `.mjs` |
| MCP tools (`worker_enqueue`, `worker_list`, `worker_show`, `worker_dry_run_next`, `worker_trust_list`, `worker_trust_check`) | OpenClaw extension at `.openclaw/extensions/agent-worker/` | **Bridge (M5.4):** extension rewritten to call this layer's `worker.sh` |
| `running/` state | yes | re-add to this layer (M5.1) — `.mjs` still needs it for in-flight tasks during execution |

## What this layer keeps as canonical

These are not affected by the bridge — they're the value this layer brings on top of `.mjs`:

- `awaiting-approval` state + `approve` / `reject` commands (race-locked per P3 #2)
- Atomic claim via `os.rename` (M4.1)
- `claimed_by` enforcement on submit (M4.1)
- Per-task `audit.transitions` array
- `approval.resolved_at/by/decision` post-submit stamping (P3 #1)
- `adapters/openclaw/queue_client.py` reference Python client
- Versioned JSON Schemas at `core/schemas/`
- `docs/artifacts/` evidence convention
- `scripts/doctor.sh` instrumentation (extends to surface bridge health)

## Vocabulary alignment (still needed across the bridge)

`.mjs` `mode` ↔ this layer `kind`:

| `.mjs` | this layer | Action in M5.1 |
|---|---|---|
| `review` | `code-review` | accept the rename in bridge translation |
| `debug` | _(new)_ | **add `debug` to this layer's kind enum** |
| `implement` | `code-change` | accept the rename |
| `research` | `research` | identity |
| `explain` | `explain` | identity |
| _(absent)_ | `test-run` / `artifact-generation` / `data-extraction` / `other` | this layer keeps these; `.mjs` doesn't need to know about them (M5.4 MCP surface decides whether to expose) |

`.mjs` `filesystem` ↔ this layer `trust_level`:

| `.mjs` | this layer | Action in M5.1 |
|---|---|---|
| `read_only` | `read-only` | identity |
| `workspace_write` | `scoped-write` | accept as default mapping; allow operator to override to `repo-write` if a `.mjs` task explicitly needs commit permission |
| _(absent)_ | `repo-write` / `elevated` | this layer keeps; `.mjs` doesn't need them |
| `dangerous_bypass: true` | _refused_ | both systems already refuse |

**Per-task permissions vs policy ceilings.** `.mjs`'s `task.permissions` block (`filesystem`, `network`, `can_run_tests`, `dangerous_bypass`) is distinct from this layer's trust-policy ceilings. M5.1 adds a `permissions` field to `worker-task.schema.json` to carry the `.mjs` per-task block intact when bridging, without conflating it with the policy-side `trust_level`.

## Sub-milestone breakdown (operator's outline)

### M5.0 — this plan (no code)

The current commit. Closes once operator signs off.

### M5.1 — minimal schema/vocab alignment for bridge compatibility

Only what M5.2/M5.3 will need to translate cleanly. Resist the urge to do "while we're here" cleanups.

- Re-add `running` to `worker-task.schema.json` status enum + `STATES` tuple in `_worker.py` + `queue/running/` directory + doctor's `check_queue`
- Add `debug` to `KIND` enum in `worker-task.schema.json` + `_worker.py` + `queue_client.py` Literal
- Add `permissions` object on `worker-task.schema.json` (`filesystem`, `network`, `can_run_tests`, `dangerous_bypass`) — preserved verbatim, not interpreted by this layer's policy resolver
- Port `worker-result.schema.json` from `~/.openclaw/workspace/worker-tasks/schemas/` to `core/schemas/worker-result.schema.json`, keep `$id` and shape identical so `.mjs`'s outputs validate
- Doctor `check_repo` learns to validate the new schema too

### M5.2 — bridge data-plane operations in `.mjs` to `worker.sh`

In `.mjs`, replace direct filesystem operations with shell-outs to `worker.sh`. Keep `.mjs`'s CLI surface stable so its existing callers (operator manual use + extension MCP tools) keep working unchanged from their perspective.

- `agent-worker.mjs enqueue` → wraps `worker.sh enqueue` with field translation per the vocab table
- `agent-worker.mjs list` → wraps `worker.sh list`
- `agent-worker.mjs show` → wraps `worker.sh show`
- `agent-worker.mjs trust-list / trust-add / trust-check / trust-remove` → wraps `worker.sh trust-*` (this layer adds these subcommands in M5.2 as part of the bridge — currently we only have the policy file, no CLI)
- `agent-worker.mjs doctor` and `init` → stay local to `.mjs` (no bridge needed)

Result of M5.2: enqueue/list/show/trust go through this layer's queue. `.mjs` is a thin wrapper for those operations. `run-next` and `run <id>` still use `.mjs`'s local logic (M5.3 work).

### M5.3 — keep execution in `.mjs`, but read from the bridged queue

`.mjs`'s `run-next` / `run <id>` are now reading from `~/.cache/agent-continuity/queue/queued/` instead of `~/.openclaw/workspace/worker-tasks/pending/`. Two concrete changes inside `.mjs`:

- `findTask(id)` / pending-task discovery: shell out to `worker.sh list --state=queued --json` and pick by `created_at`
- After execution completes: instead of writing to `done/<id>.json` directly, call `worker.sh submit --worker <worker-id> --result <result-file>`

Everything between "task picked" and "result written" — the codex/claude subprocess invocation, prompt building, timeout, result normalization — stays in `.mjs` unchanged. No port.

The `running/` state transition is what `.mjs` uses while the subprocess is executing. M5.1 makes `running/` exist here; M5.3 has `.mjs` use it by calling `worker.sh` claim (which moves queued→claimed) followed by a separate "mark running" transition. We may need a new `worker.sh start --task-id` subcommand for the claimed→running transition; flagging this as the one extra surface M5.3 likely needs.

### M5.4 — MCP tool surface for Mika

`~/.openclaw/workspace/.openclaw/extensions/agent-worker/` currently registers tools (`worker_enqueue`, `worker_list`, `worker_show`, `worker_dry_run_next`, `worker_trust_list`, `worker_trust_check`) that Mika calls. After M5.2 those tools work because `.mjs` is bridged underneath. M5.4 explicitly verifies and, if helpful, adds a new extension at `.openclaw/extensions/agent-continuity/` that exposes the same tool names but bypasses `.mjs` entirely (calls `worker.sh` directly). The duplicate-name choice means Mika's MCP config can switch by editing the extension reference, not the tool names — zero-downtime cutover possible.

Decision deferred to M5.4 itself: do we keep the `.mjs` extension as the operator-facing surface, or cut over to the direct-call extension?

### M5.5 — Replace decision point

After M5.1–M5.4 are running in production, evaluate:

- Is the bridge stable? Any operations that round-trip awkwardly through `.mjs`?
- Does `.mjs` still earn its keep, or has its job shrunk to "subprocess spawner with `.mjs`-shaped baggage"?
- What's the marginal cost of porting execution to Python here vs maintaining the bridge?

If bridge wins long-term: M5 closes here. `.mjs` stays as the execution engine, this layer stays as the queue/trust/audit canonical store.

If port wins: trigger the original M5.3/M5.6/M5.7 work from the Replace plan (`955a01d`), now informed by real bridge experience instead of speculation.

---

## M5.5 decision: **Bridge wins. Port deferred indefinitely.**

Recorded post-M5.4 (commit `5f866db`). Rationale, in the operator's words:

- Bridge works in production path
- Mika MCP tools verified end-to-end (M5.4)
- Real Codex execution succeeded through the bridge (M5.3 real-run validation, `task-e49c5caffbea`)
- Canonical records now include audit + result + process metadata (M5.3c)
- Porting subprocess execution would consolidate implementation but does NOT strengthen any continuity primitive — it's engineering tidiness, not product progress

Under the charter (`CHARTER.md`) milestone rule, Replace doesn't qualify as continuity work. Defer until execution drift becomes a real maintenance burden — at which point the original Replace plan (commit `955a01d`) is still preserved in git history as a known-good fallback.

**M5 is closed.** Next: M6 (Charter Enforcement + Memory Inventory) per `docs/roadmap.md`.

## Migration plan (data)

### Trust grants

`~/.openclaw/workspace/worker-tasks/trust-policy.json` is `{"version": 1, "grants": []}` (empty). The M5.2 work adds a small migration step that reads any grants that appear there and translates to this layer's shape. Dry-run by default.

### In-flight tasks

`worker-tasks/pending/` and `running/` are empty as of this discovery. If tasks accumulate before M5.3 ships, M5.3 reads them and finishes them in `.mjs`'s native path; new tasks land in this layer's queue.

### Completed tasks (`done/`, `failed/`)

3+ records exist in `worker-tasks/done/`. Two options:

- **Leave in place:** `.mjs` retains its done history; this layer's queue starts fresh from M5.3 onward. Two histories, one cutover date.
- **Archive to `docs/artifacts/m5-import/`:** the M4.6 cleanup pattern. Preserves evidence; doesn't migrate format. Recommended.

M5.2 implements the archive option as part of the trust-policy migration.

## Out of scope (deliberately)

Same as the previous draft:

- Network access for workers (`.mjs` hard-codes `network: off`; this layer continues to refuse `network: on`)
- `dangerous_bypass` (refused in both systems)
- Browser / non-terminal workers
- Cross-machine worker dispatch
- VM / `life-agents.json` flow — the cross-device-continuity vision from v0.1 (this repo's original charter) remains separate from M5

## Risks (bridge-specific)

| Risk | Mitigation |
|---|---|
| `.mjs` and this layer have slightly different task JSON shapes; round-tripping loses info | M5.1 adds `permissions` block to absorb `.mjs`'s per-task settings verbatim |
| Mika's MCP tools depend on `.mjs`-side files (e.g. `worker-tasks/done/<id>.json`) for `worker_show` | M5.2 ensures `worker.sh show` returns a `.mjs`-compatible response shape OR M5.4 cuts MCP over to read from this layer's queue |
| `running/` lifecycle requires coordination — `.mjs` claims, `.mjs` runs, `.mjs` submits, but the queue is here | M5.3 likely adds `worker.sh start` subcommand (claimed → running); audit captures both transitions |
| Vocabulary translation invisibly drops a field (e.g. `.mjs` `can_run_tests`) | M5.1's `permissions` block preserves `.mjs` fields verbatim; M5.2's enqueue wrapper passes them through |
| Bridge proves more complex than Replace would have been | M5.5 explicitly re-evaluates; Replace stays a known-good fallback (the plan from commit `955a01d` is still in git) |

## Open observation (not in M5 scope)

This repo was originally framed as **shared memory + project registry + cross-device continuity** (v0.1's `life-agents-unified.skill`). Over M0–M4 the focus drifted to a worker queue (which collided with `.mjs` — hence M5). The actual cross-device continuity surface (`sync.sh`, M3) is comparatively thin. Whether to re-prioritize the v0.1 charter after M5 closes is a separate strategic call.

## Sign-off

M5.1 will not start until the operator approves this revised plan (or asks for further revisions). The current commit ends here.
