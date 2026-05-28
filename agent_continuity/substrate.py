"""Thin Python wrapper over the agent-continuity CLI.

M13.2 scope: expose the six adapter-contract operations through a small
Python object without creating a second implementation layer. Every write
continues to route through existing scripts (`mcp.sh tool`, `worker.sh`,
`decisions.sh`) via the installed `agent-continuity` dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping, Sequence


class SubstrateError(RuntimeError):
    """Base SDK exception."""


class SubstrateCommandError(SubstrateError):
    """Raised when the underlying CLI exits non-zero."""

    def __init__(self, command: Sequence[str], returncode: int, stdout: str, stderr: str):
        self.command = tuple(command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        msg = (
            f"agent-continuity command failed rc={returncode}: "
            f"{' '.join(self.command)}"
        )
        detail = (stderr or stdout).strip()
        if detail:
            msg += f"\n{detail}"
        super().__init__(msg)


@dataclass(frozen=True)
class CommandResult:
    """Raw command result for callers that need the transport details."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Substrate:
    """SDK entry point.

    Parameters:
        command: Explicit command to run. Defaults to `agent-continuity` on PATH
            or the repo/install-local `bin/agent-continuity` next to this package.
        root: Optional repo or install root. When provided, uses
            `<root>/bin/agent-continuity`.
        env: Environment overrides. Values are merged on top of `os.environ`.
        timeout: Subprocess timeout in seconds.
    """

    def __init__(
        self,
        *,
        command: str | Path | Sequence[str] | None = None,
        root: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = 30,
    ) -> None:
        if command is not None and root is not None:
            raise ValueError("pass either command or root, not both")
        self.command = self._resolve_command(command=command, root=root)
        merged_env = os.environ.copy()
        if env:
            merged_env.update({str(k): str(v) for k, v in env.items()})
        self.env = merged_env
        self.timeout = timeout

    @staticmethod
    def _resolve_command(
        *, command: str | Path | Sequence[str] | None, root: str | Path | None
    ) -> tuple[str, ...]:
        if command is not None:
            if isinstance(command, (str, Path)):
                return (str(command),)
            return tuple(str(x) for x in command)
        if root is not None:
            return (str(Path(root) / "bin" / "agent-continuity"),)
        on_path = shutil.which("agent-continuity")
        if on_path:
            return (on_path,)
        # In-tree / extracted-tarball fallback: package lives at
        # <root>/agent_continuity, binary at <root>/bin/agent-continuity.
        package_root = Path(__file__).resolve().parent.parent
        candidate = package_root / "bin" / "agent-continuity"
        return (str(candidate),)

    @classmethod
    def from_repo(
        cls, root: str | Path, *, env: Mapping[str, str] | None = None, timeout: int = 30
    ) -> "Substrate":
        return cls(root=root, env=env, timeout=timeout)

    @classmethod
    def from_command(
        cls, command: str | Path | Sequence[str], *, env: Mapping[str, str] | None = None,
        timeout: int = 30
    ) -> "Substrate":
        return cls(command=command, env=env, timeout=timeout)

    def run(self, *args: str, input_text: str | None = None, check: bool = True) -> CommandResult:
        cmd = (*self.command, *[str(a) for a in args])
        proc = subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=self.timeout,
        )
        result = CommandResult(cmd, proc.returncode, proc.stdout, proc.stderr)
        if check and proc.returncode != 0:
            raise SubstrateCommandError(cmd, proc.returncode, proc.stdout, proc.stderr)
        return result

    def _json(self, *args: str) -> Any:
        result = self.run(*args)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise SubstrateError(
                f"command did not return JSON: {' '.join(result.args)}\n{e}\n{result.stdout[:500]}"
            ) from e

    def version(self) -> str:
        return self.run("--version").stdout.strip()

    def doctor(self) -> dict[str, Any]:
        return self._json("doctor", "--json")

    def mcp_tool(self, name: str, args: Mapping[str, Any] | None = None) -> Any:
        payload = json.dumps(dict(args or {}), separators=(",", ":"))
        return self._json("mcp", "tool", name, "--args", payload)

    # Six M9 adapter-contract operations.

    def whoami(self) -> dict[str, Any]:
        return self.mcp_tool("whoami")

    def read_context(self) -> dict[str, Any]:
        return self.mcp_tool("read_context", {"format": "json"})

    def read_decisions(
        self,
        *,
        repo: str | None = None,
        adapter: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        args: dict[str, Any] = {}
        if repo is not None:
            args["repo"] = repo
        if adapter is not None:
            args["adapter"] = adapter
        if limit is not None:
            args["limit"] = limit
        result = self.mcp_tool("read_decisions", args)
        if not isinstance(result, list):
            raise SubstrateError(f"read_decisions returned {type(result).__name__}, expected list")
        return result

    def append_decision(
        self,
        *,
        decision: str,
        why: str,
        adapter: str = "human",
        author: str | None = None,
        repo: str | None = None,
        refs: Sequence[str] | None = None,
    ) -> str:
        args: dict[str, Any] = {
            "decision": decision,
            "why": why,
            "adapter": adapter,
        }
        if author is not None:
            args["author"] = author
        if repo is not None:
            args["repo"] = repo
        if refs is not None:
            args["refs"] = list(refs)
        result = self.mcp_tool("append_decision", args)
        try:
            return str(result["decision_id"])
        except (TypeError, KeyError) as e:
            raise SubstrateError(f"append_decision returned unexpected result: {result!r}") from e

    def claim_task(
        self,
        *,
        adapter: str,
        as_adapter_id: str,
        kind: str | None = None,
        repo: str | None = None,
        trust_level: str | None = None,
    ) -> dict[str, Any] | None:
        args: dict[str, Any] = {"adapter": adapter, "as_adapter_id": as_adapter_id}
        if kind is not None:
            args["kind"] = kind
        if repo is not None:
            args["repo"] = repo
        if trust_level is not None:
            args["trust_level"] = trust_level
        result = self.mcp_tool("claim_task", args)
        if result is None:
            return None
        if not isinstance(result, dict):
            raise SubstrateError(f"claim_task returned {type(result).__name__}, expected object/null")
        return result

    def submit_result(
        self,
        *,
        task_id: str,
        result: Mapping[str, Any],
        as_adapter_id: str,
        needs_approval: bool = False,
        fail: bool = False,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "task_id": task_id,
            "result": dict(result),
            "as_adapter_id": as_adapter_id,
        }
        if needs_approval:
            args["needs_approval"] = True
        if fail:
            args["fail"] = True
        payload = self.mcp_tool("submit_result", args)
        if not isinstance(payload, dict):
            raise SubstrateError(f"submit_result returned {type(payload).__name__}, expected object")
        return payload
