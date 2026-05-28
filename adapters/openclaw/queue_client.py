"""Reference Python client for the agent-continuity worker queue.

OpenClaw should call these functions when Mika decides a task needs to be
delegated to a worker. This client invokes scripts/worker.sh as a subprocess
so the CLI surface stays the integration boundary — no logic is duplicated
here, and the worker.sh contract is the only API surface OpenClaw depends on.

Import + use, or copy + adapt. The patterns this module encodes:

  - All operations go through worker.sh --json so the caller gets structured
    output. Errors are raised as exceptions (PolicyError, UsageError,
    WorkerError) rather than returned as sentinel values.
  - OpenClaw is the control plane: this module exposes enqueue/list/show/
    approve/reject. claim and submit are intentionally NOT exposed —
    OpenClaw never claims its own tasks and never submits results; workers
    (Claude, Codex) do.
  - source_adapter defaults to "openclaw" and source_actor defaults to "mika"
    so audit trails attribute the right caller without per-call boilerplate.

Example:

    from adapters.openclaw import queue_client as q

    result = q.enqueue(
        project="proj-life-agent",
        kind="code-change",
        target="codex",
        trust_level="scoped-write",
        instruction="Append a TODO line to docs/roadmap.md",
        repo="file:///path/to/fixture",
        branch="main",
        files_allowed=["docs/roadmap.md"],
        expected_artifacts=[{"kind": "patch", "path": "docs/roadmap.md.diff"}],
    )
    # If status == 'awaiting-approval', operator (Mika) decides:
    if result["status"] == "awaiting-approval":
        q.approve(result["task_id"], by="mika", note="auto-approved by Mika policy v1")

    # Later, list completed tasks for reporting:
    for t in q.list_tasks(state="completed")["by_state"]["completed"]:
        print(t["id"], t["kind"], t["claimed_by"])
"""

from __future__ import annotations
import json
import subprocess
from pathlib import Path
from typing import Any, Literal, TypedDict

# Resolve worker.sh once at import time. adapters/openclaw/queue_client.py
# is two levels under the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKER_SH = REPO_ROOT / "scripts" / "worker.sh"


# ── type aliases mirroring worker-task.schema.json enums ───────────────────

Kind = Literal[
    "code-change", "code-review", "debug", "research", "explain",
    "test-run", "artifact-generation", "data-extraction", "other",
]
TrustLevel = Literal["read-only", "scoped-write", "repo-write", "elevated"]
WorkerAdapter = Literal["claude", "codex"]
TaskState = Literal[
    "queued", "claimed", "running", "completed", "awaiting-approval",
    "rejected", "failed", "cancelled",
]


class ExpectedArtifact(TypedDict):
    kind: Literal["file", "patch", "report", "decision-entry", "test-result"]
    path: str


class Permissions(TypedDict, total=False):
    """Per-task permissions preserved verbatim on the task (M5.1.1). Mirrors
    .mjs's permissions block. dangerous_bypass=true and network!='off' are
    refused at enqueue by this layer; .mjs enforces them at execution time."""
    filesystem: Literal["read_only", "workspace_write"]
    network: Literal["off"]
    can_run_tests: bool
    dangerous_bypass: bool
    timeout_sec: int


class EnqueueResult(TypedDict, total=False):
    task_id: str
    status: TaskState
    matched_repo: str | None
    reasons: list[str]
    path: str


# ── exceptions ─────────────────────────────────────────────────────────────

class WorkerError(Exception):
    """Base class for worker queue errors. Carries the rc and parsed report."""
    def __init__(self, rc: int, report: dict[str, Any]):
        self.rc = rc
        self.report = report
        super().__init__(report.get("error", f"worker.sh exited rc={rc}"))


class PolicyError(WorkerError):
    """rc=2: task rejected by trust policy, or state-transition violation
    (e.g. trying to claim a task that's not in queued/, or submit by a
    different worker than claimed_by)."""


class UsageError(WorkerError):
    """rc=64: invalid CLI arguments (mutually exclusive flags, malformed
    expected-artifact, etc.). Indicates a bug in the caller, not a runtime
    condition."""


# ── core ───────────────────────────────────────────────────────────────────

def _run(args: list[str], stdin_text: str | None = None) -> tuple[int, dict[str, Any]]:
    """Invoke worker.sh --json with the given subcommand args. Returns
    (returncode, parsed_report). Never raises; caller decides via _check."""
    cmd = [str(WORKER_SH), "--json"] + args
    p = subprocess.run(
        cmd, input=stdin_text, capture_output=True, text=True,
    )
    try:
        report = json.loads(p.stdout)
    except json.JSONDecodeError:
        report = {
            "error": f"worker.sh returned non-JSON output: {p.stdout[:500]!r}",
            "stderr_tail": p.stderr[-500:],
        }
    return p.returncode, report


def _check(rc: int, report: dict[str, Any]) -> dict[str, Any]:
    if rc == 0:
        return report
    if rc == 2:
        raise PolicyError(rc, report)
    if rc == 64:
        raise UsageError(rc, report)
    raise WorkerError(rc, report)


# ── public surface ─────────────────────────────────────────────────────────

def enqueue(
    *,
    project: str,
    kind: Kind,
    target: WorkerAdapter,
    trust_level: TrustLevel,
    instruction: str,
    repo: str | None = None,
    branch: str | None = None,
    files_allowed: list[str] | None = None,
    expected_artifacts: list[ExpectedArtifact] | None = None,
    permissions: Permissions | None = None,
    source_adapter: str = "openclaw",
    source_actor: str = "mika",
) -> EnqueueResult:
    """Enqueue a worker-task. Raises PolicyError if the trust policy rejects
    it at enqueue time, OR if `permissions` contains dangerous_bypass=true
    or network!='off' (both unconditionally refused). Returns the report
    including task_id and routed status (queued or awaiting-approval).

    `instruction` is the worker's brief, sent via stdin so the caller doesn't
    have to manage a temp file."""
    args = [
        "enqueue",
        "--project", project,
        "--kind", kind,
        "--target", target,
        "--trust-level", trust_level,
        "--instruction", "-",
        "--source-adapter", source_adapter,
        "--source-actor", source_actor,
    ]
    if repo:
        args += ["--repo", repo]
    if branch:
        args += ["--branch", branch]
    if files_allowed:
        args += ["--files-allowed", *files_allowed]
    if expected_artifacts:
        args += ["--expected-artifact"] + [
            f"{a['kind']}:{a['path']}" for a in expected_artifacts
        ]
    if permissions is not None:
        args += ["--permissions", json.dumps(permissions)]
    rc, report = _run(args, stdin_text=instruction)
    return _check(rc, report)  # type: ignore[return-value]


def list_tasks(state: TaskState | None = None) -> dict[str, Any]:
    """List tasks grouped by state. Pass `state` to limit to one bucket."""
    args = ["list"]
    if state:
        args += ["--state", state]
    return _check(*_run(args))


def show(task_id: str) -> dict[str, Any]:
    """Return the full task JSON + audit transitions."""
    return _check(*_run(["show", task_id]))


def approve(task_id: str, *, by: str, note: str | None = None) -> dict[str, Any]:
    """Approve an awaiting-approval task. Pre-claim → queued; post-submit →
    completed. Distinguished by presence of task.result."""
    args = ["approve", task_id, "--by", by]
    if note:
        args += ["--note", note]
    return _check(*_run(args))


def reject(task_id: str, *, by: str, reason: str | None = None) -> dict[str, Any]:
    """Reject an awaiting-approval task. Pre-claim → cancelled; post-submit
    → rejected. Distinguished by presence of task.result."""
    args = ["reject", task_id, "--by", by]
    if reason:
        args += ["--reason", reason]
    return _check(*_run(args))


__all__ = [
    "enqueue", "list_tasks", "show", "approve", "reject",
    "Kind", "TrustLevel", "WorkerAdapter", "TaskState",
    "ExpectedArtifact", "Permissions",
    "EnqueueResult",
    "WorkerError", "PolicyError", "UsageError",
    "REPO_ROOT", "WORKER_SH",
]
