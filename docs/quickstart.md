# Quickstart

Six commands. No VM, no OpenClaw, no Anthropic/OpenAI API key, no real LLM. Everything runs in a sandbox under `~/.{config,local/state,cache}/agent-continuity-quickstart/` — your real continuity-layer state (if you have any) is never touched.

The last command shows you the **thing that survived**: a durable, attributed decision recorded by a delegated worker, readable here, readable in three months by a different agent, with the *why* preserved.

## The path

```
git clone <repo-url>
cd agent-continuity-layer

scripts/quickstart.sh init
scripts/quickstart.sh doctor
scripts/quickstart.sh enqueue
scripts/quickstart.sh run-fake-worker
scripts/quickstart.sh decisions list
```

That's it. Read `docs/quickstart.md` (this file) only if you want context; otherwise the commands print enough output to follow along.

## What just happened

1. **`init`** copies `fixtures/quickstart-project/` into a sandbox workspace, `git init`s it, and writes a starter trust policy. The policy is **time-limited (30 days), scoped to `quickstart-project`, research-kind only, read-only trust level, codex-adapter only.** Even a buggy fake worker can't do anything dangerous under this policy.
2. **`doctor`** verifies the sandbox is healthy — trust policy present, schemas valid, scripts executable, no leftover state from a previous run.
3. **`enqueue`** creates a research-kind task in the sandbox queue, scoped to the `quickstart-project` repo. Idempotent: re-running surfaces the existing task instead of creating a duplicate. Pass `--new` to force a fresh task.
4. **`run-fake-worker`** invokes `scripts/_quickstart_fake_worker.sh`, a transparent ~50-line shell script that does the worker dance: `claim → start → submit`. The submit carries a pre-canned `worker-result` with one embedded decision. **No real LLM is invoked.** The fake worker is illustration — you can read its source to see exactly what a delegated-worker submission looks like.
5. **`decisions list`** reads the canonical decisions log under the sandbox env and prints the decision the fake worker just appended.

## What survived

After the five commands above, `scripts/quickstart.sh decisions list` shows:

```
2026-XX-XXTHH:MM:SSZ  [codex]  repo=quickstart-project  id=<sha256>
  decision: Fixture worker reviewed the quickstart task and confirmed the M11
            substrate preserves attributed reasoning from a delegated worker.
  why     : The quickstart's purpose is to demonstrate that a delegated worker
            can append a durable decision the operator can read minutes (or
            months) later, with full provenance and no operator intervention
            beyond running the script. This decision is that demonstration —
            its existence in the canonical log IS the proof.
  refs    : task:task-<id>, M11.1, doc:docs/north-star.md
```

The fields that matter:

| field | value | why it matters |
|---|---|---|
| `adapter` | `codex` | the canonical adapter brand — same value across shell, MCP, bundle, bridge transports (M9) |
| `repo` | `quickstart-project` | derived from `task.input.repo`; trust grant matched on this same name |
| `refs[0]` | `task:task-<id>` | **auto-injected** by the submit handler. Any future agent reading this entry can `worker.sh show <id>` to see the full task that produced it |
| `id` | sha256 of canonical body | content-addressed (M8.0). Identical decisions get identical ids, which makes dedup trivial across devices |

The decision is now a permanent row in the sandbox's `decisions.jsonl`. A different agent running `scripts/quickstart.sh decisions list` later — different model, different session, different chat surface — sees the same row with the same attribution.

## Where it lives

Everything the quickstart creates lives under three sandbox directories. Your real continuity-layer state (if you have any) is at `~/.config/agent-continuity/`, `~/.local/state/agent-continuity/`, `~/.cache/agent-continuity/` (no `-quickstart` suffix) and is not touched.

| Sandbox path | Contents |
|---|---|
| `~/.config/agent-continuity-quickstart/agent-continuity/trust-policy.json` | The starter trust policy (time-limited, repo-scoped) |
| `~/.local/state/agent-continuity-quickstart/agent-continuity/decisions.jsonl` | The decisions log — the durable file with the row above |
| `~/.local/state/agent-continuity-quickstart/workspace/quickstart-project/` | The fixture repo (git init'd, one commit) |
| `~/.local/state/agent-continuity-quickstart/quickstart-state.json` | The fixture task id (used by `enqueue` / `run-fake-worker` for idempotency) |
| `~/.cache/agent-continuity-quickstart/agent-continuity/queue/` | The sandbox worker queue. After `run-fake-worker`, the fixture task lives under `completed/` |

To inspect any of these directly without going through `quickstart.sh`, eval the env block:

```
eval "$(scripts/quickstart.sh env)"
scripts/decisions.sh list --json
scripts/worker.sh --json list
```

## Why this is not OpenClaw/Mika-specific

The quickstart deliberately exercises ZERO of the OpenClaw/Mika code path:

- **No OpenClaw daemon** is required or referenced.
- **No `.mjs` bridge** is invoked (`scripts/_quickstart_fake_worker.sh` is a plain bash script).
- **No Anthropic or OpenAI API key** is read.
- **No Claude or Codex CLI binary** is executed.
- The fake worker is ~50 lines of shell that calls `worker.sh claim/start/submit` — exactly what any future adapter would call.

The continuity layer is the *substrate*. OpenClaw is one adapter that happens to run on top of it. The quickstart proves the substrate works without any specific adapter.

## How to reset

```
scripts/quickstart.sh reset
```

Removes the three sandbox directories (`~/.config/agent-continuity-quickstart/`, `~/.local/state/agent-continuity-quickstart/`, `~/.cache/agent-continuity-quickstart/`). Idempotent — running on a clean machine prints "nothing to remove" and exits 0.

Safety: the reset CLI refuses to remove any path that isn't shaped like a quickstart sandbox path (must end with `/agent-continuity-quickstart`, must be under `$HOME`, must not contain `/agent-continuity/` as a parent). The sandbox name is deliberately distinct from the real namespace (`agent-continuity`) so accidental deletion of real state is impossible by construction.

To preview what would be removed:

```
scripts/quickstart.sh reset --dry-run
```

To clear and start over in one command:

```
scripts/quickstart.sh reset --reinit
```

Equivalent to `reset` followed by `init`.

## How to graduate to a real Codex or Claude worker

The fake worker exists so the quickstart runs without API keys or CLI binaries. When you're ready to use a real Codex or Claude worker against one of your own repos:

1. Read `docs/walkthroughs/codex-local-shell.md` (or `claude-web-bundle.md` for the web flow).
2. The walkthrough uses the **real namespace** (`~/.config/agent-continuity/`, not the `-quickstart` sandbox) — that's where your durable continuity lives.
3. The same `worker.sh claim / start / submit` chain applies; only the *who* claiming the task changes (a real Codex CLI instead of `_quickstart_fake_worker.sh`).

The fixture project, fake worker, and sandboxed trust policy stay in this repo for the next visitor.

## See also

- [`north-star.md`](north-star.md) — long-term vision (read this if you want *why* before *how*)
- [`../CHARTER.md`](../CHARTER.md) — the canonical product charter
- [`walkthroughs/`](walkthroughs/) — adapter-specific walkthroughs (pick the host you actually have)
- [`m9-adapter-pattern.md`](m9-adapter-pattern.md) — the six-operation contract that makes adapters interchangeable
- [`handoff-vs-continuity.md`](handoff-vs-continuity.md) — why the worker queue is a subsystem, not the product
