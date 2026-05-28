# Python SDK

M13.2 adds a tiny Python wrapper over the existing `agent-continuity` CLI.
It is intentionally thin: the SDK calls the same M9/M13 operation surface as
shell, MCP, bundle, and bridge transports. It does not reimplement the queue,
decision log, trust policy, or context semantics.

```python
from agent_continuity import Substrate

substrate = Substrate()
print(substrate.version())
print(substrate.read_context()["identity"]["project_name"])

decision_id = substrate.append_decision(
    adapter="human",
    repo="my-project",
    decision="Keep release artifacts versioned under XDG_DATA_HOME.",
    why="Install rollback must not move operator state.",
    refs=["M12.1"],
)
print(decision_id)
```

## Construction

```python
Substrate()                         # use `agent-continuity` on PATH, or local repo fallback
Substrate(root="/path/to/install")  # use /path/to/install/bin/agent-continuity
Substrate(command="/custom/bin/agent-continuity")
```

The SDK package ships in the repo and release tarball, but M13.2 does not
install a Python package into site-packages. Use it from a repo checkout, vendor
`agent_continuity/` into your adapter, or add the extracted install root to
`PYTHONPATH` before importing:

```bash
export PYTHONPATH="/path/to/agent-continuity-v0.1.2:${PYTHONPATH:-}"
python3 -c 'from agent_continuity import Substrate; print(Substrate().version())'
```

Pass `env={...}` to sandbox XDG paths or backend env vars:

```python
substrate = Substrate(env={
    "XDG_CONFIG_HOME": "/tmp/ac/config",
    "XDG_STATE_HOME": "/tmp/ac/state",
    "XDG_CACHE_HOME": "/tmp/ac/cache",
})
```

## Six operations

The SDK exposes the six adapter-contract operations from
[`docs/m9-adapter-pattern.md`](m9-adapter-pattern.md):

```python
substrate.whoami()
substrate.read_context()
substrate.read_decisions(repo="my-project", adapter="human", limit=10)
substrate.append_decision(adapter="human", decision="...", why="...")
substrate.claim_task(adapter="codex", as_adapter_id="my-worker", kind="research")
substrate.submit_result(task_id="task-...", result={"summary": "..."}, as_adapter_id="my-worker")
```

Under the hood these call:

```bash
agent-continuity mcp tool <operation> --args '<json>'
```

That means SDK behavior stays compatible with the MCP server, shell tooling,
and adapter bundle semantics.

## Error handling

Non-zero CLI exits raise `SubstrateCommandError` with:

- `command`
- `returncode`
- `stdout`
- `stderr`

Malformed JSON or unexpected response shapes raise `SubstrateError`.

## Smoke test

From the repo or extracted install:

```bash
python3 scripts/_sdk_smoke.py
```

The smoke runs in temporary XDG directories, appends one decision, enqueues a
fixture research task, claims it through the SDK, submits a worker-result with
an embedded decision, and verifies both decisions survive in the sandbox log.
