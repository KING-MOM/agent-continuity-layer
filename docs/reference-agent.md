# Reference Agent Demo

M13.3 adds the smallest useful local agent shape:

```text
read_context -> decide -> append_decision
```

No LLM is invoked. No worker task is claimed. No OpenClaw bridge is involved.
The demo exists to show how a Python agent can participate in continuity using
`from agent_continuity import Substrate` without learning the repo's internal
scripts.

## Run It

Dry-run first:

```bash
agent-continuity reference-agent --dry-run
```

Append the decision:

```bash
agent-continuity reference-agent
agent-continuity decisions list --adapter codex --limit 1
```

The default adapter token is `codex` because the decision-entry schema has a
small explicit adapter enum: `claude`, `codex`, `openclaw`, `human`,
`chatgpt`, `gemini`, `grok`, `kimi`. The demo
sets `author=reference-agent-demo` so readers can tell this was the deterministic
reference agent, not a real Codex CLI invocation.

## What It Writes

The decision says the reference agent read the current context snapshot and
confirmed the SDK can append durable decisions without an LLM. Its `why` field
names the M13.3 purpose: prove the smallest useful agent loop through the same
canonical decision log future agents read.

Refs include:

- `M13.3`
- `doc:docs/python-sdk.md`
- `doc:docs/north-star.md`

## Smoke Test

From the repo or extracted install:

```bash
python3 scripts/_reference_agent_smoke.py
```

The smoke runs in temporary XDG directories, performs a dry-run, appends one
real sandboxed decision, and verifies that `decisions list` can read it back with
adapter, author, and refs intact.
