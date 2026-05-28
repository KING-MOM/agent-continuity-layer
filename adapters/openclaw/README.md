# adapters/openclaw

**Role:** Control plane. The boss.

OpenClaw owns:
- Mika (the social interface)
- Channel ingress (messaging channel, Telegram, web)
- Routing decisions (which worker, which trust level)
- Human approvals
- The worker task queue (enqueue + dispatch + audit)
- Trust policy resolution

## Interfaces this adapter implements

| Operation | Direction | Surface |
|---|---|---|
| Read project registry | VM → here | `core/schemas/project-registry.schema.json` |
| Write project registry | here → VM | exclusive writer |
| Enqueue worker task | here → queue | emits `core/schemas/worker-task.schema.json` |
| Receive task result | worker → here | validates result against `expected_artifacts` |
| Read trust policy | local file | `core/schemas/trust-policy.schema.json` |
| Approve dangerous task | human → here | gates `awaiting-approval` → `claimed` |

## What it does NOT do

- Does not write code in workspace repos. That's the workers' job.
- Does not run skills from `.claude/` or `.codex/`. Those are worker concerns.
- Does not auto-sync skills from the VM to worker devices.

## Status

Reference Python client lives at [`queue_client.py`](queue_client.py) — a subprocess-driven wrapper over `scripts/worker.sh` with typed signatures and an exception hierarchy. OpenClaw's daemon should `import` it (or copy + adapt) when wiring Mika's routing layer to the worker queue. The CLI surface (`worker.sh`) remains the integration boundary; no queue logic is duplicated in Python.

Quick example:

```python
from adapters.openclaw import queue_client as q

try:
    res = q.enqueue(
        project="proj-life-agent",
        kind="code-change",
        target="codex",
        trust_level="scoped-write",
        instruction="Append a TODO to docs/roadmap.md",
        repo="file:///path/to/fixture",
        files_allowed=["docs/roadmap.md"],
        expected_artifacts=[{"kind": "patch", "path": "docs/roadmap.md.diff"}],
    )
    if res["status"] == "awaiting-approval":
        q.approve(res["task_id"], by="mika", note="auto-approved by policy")
except q.PolicyError as e:
    # Trust policy rejected the task. e.report['reasons'] explains why.
    ...
```

The full daemon wire-up (Mika decides → calls `q.enqueue` → operator/Mika `q.approve` → worker picks up) lives in the OpenClaw repo, not here.
