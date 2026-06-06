#!/usr/bin/env python3
"""mcp.py — M9.2 MCP tool surface for the six adapter-contract operations.

Continuity primitive: adapter portability.

Thin wrappers over existing scripts (context.sh, decisions.sh, worker.sh).
MCP is a transport, not new orchestration — these handlers exist to prove
the M9.0 contract translates to MCP without inventing a second semantics
layer.

This module exposes:
  - load_manifest()   read core/mcp/tools.json
  - list_tools()      print the tool manifest (CLI: `mcp.sh list-tools`)
  - dispatch(name, args)
                      look up tool, validate input schema, run handler,
                      return raw result (CLI: `mcp.sh tool <name> --args`)

A full MCP server (JSON-RPC over stdio or HTTP) is intentionally not part
of M9.2. The handlers here are the operational substance; wrapping them
in MCP transport is a thin shim deferred to M9.2.x if the need surfaces.

Worker id convention for MCP-mediated writes (claim_task, submit_result):
  `mcp:<as_adapter_id>`
parallels M9.1's `bundle:<adapter_id>` so the audit trail visibly
attributes the operator-mediated MCP path.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

MANIFEST_PATH = REPO_ROOT / "core" / "mcp" / "tools.json"
IDENTITY_SCHEMA_PATH = REPO_ROOT / "core" / "schemas" / "adapter-identity.schema.json"

CONTEXT_SH = REPO_ROOT / "scripts" / "context.sh"
DECISIONS_SH = REPO_ROOT / "scripts" / "decisions.sh"
WORKER_SH = REPO_ROOT / "scripts" / "worker.sh"

_XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache"))
QUEUE_ROOT = _XDG_CACHE_HOME / "agent-continuity" / "queue"

# Reuse the canonical schema validator (same pattern as _bundle.py).
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _doctor import _validate_against_schema


# ---------- helpers ----------

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return 127, "", f"could not invoke {cmd[0]}: {e}"
    return p.returncode, p.stdout, p.stderr


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _find_tool_def(name: str) -> dict[str, Any] | None:
    manifest = load_manifest()
    for t in manifest.get("tools", []):
        if t.get("name") == name:
            return t
    return None


# ---------- handlers ----------

class MCPToolError(Exception):
    """Raised when a handler's underlying script fails. Caller surfaces
    this as the MCP tool's error response."""


def handle_whoami(_: dict[str, Any]) -> dict[str, Any]:
    """The continuity layer's self-identification descriptor. Static
    AdapterIdentity used by MCP clients to discover the layer-side
    surface."""
    return {
        "schema_version": "1.0",
        "adapter_id": "continuity-layer-local",
        "adapter_type": "local-cli",
        "adapter": "other",
        "display_name": "agent-continuity-layer (MCP surface)",
        "transport": ["mcp", "shell", "bridge", "bundle"],
        "capabilities": {
            "whoami": True,
            "read_context": True,
            "read_decisions": True,
            "append_decision": True,
            "claim_task": True,
            "submit_result": True,
        },
        "created_at": _now_iso(),
    }


def handle_read_context(args: dict[str, Any]) -> dict[str, Any]:
    fmt = args.get("format") or "json"
    if fmt != "json":
        raise MCPToolError(f"format {fmt!r} not supported in M9.2 (json only)")
    rc, out, err = _run([str(CONTEXT_SH), "--json"])
    if rc != 0:
        raise MCPToolError(f"context.sh failed (rc={rc}): {err.strip() or out.strip()}")
    return json.loads(out)


def handle_read_decisions(args: dict[str, Any]) -> list[dict[str, Any]]:
    cmd = [str(DECISIONS_SH), "list", "--json"]
    if args.get("repo"):
        cmd += ["--repo", str(args["repo"])]
    if args.get("adapter"):
        cmd += ["--adapter", str(args["adapter"])]
    if args.get("limit") is not None:
        cmd += ["--limit", str(int(args["limit"]))]
    rc, out, err = _run(cmd)
    if rc != 0:
        raise MCPToolError(f"decisions.sh list failed (rc={rc}): {err.strip()}")
    entries: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # decisions.sh list emits an info line to stderr; --json keeps
            # stdout clean, but guard anyway.
            continue
    return entries


def handle_append_decision(args: dict[str, Any]) -> dict[str, Any]:
    cmd = [
        str(DECISIONS_SH), "add",
        "--adapter", str(args["adapter"]),
        "--decision", str(args["decision"]),
        "--why", str(args["why"]),
    ]
    if args.get("author"):
        cmd += ["--author", str(args["author"])]
    if args.get("repo"):
        cmd += ["--repo", str(args["repo"])]
    for r in args.get("refs") or []:
        cmd += ["--ref", str(r)]
    rc, out, err = _run(cmd)
    if rc != 0:
        raise MCPToolError(f"decisions.sh add failed (rc={rc}): {err.strip()}")
    decision_id = (out.strip().splitlines() or [""])[0]
    if not decision_id:
        raise MCPToolError(f"decisions.sh add produced no id; stderr={err.strip()}")
    return {"decision_id": decision_id}


def _list_queued_tasks() -> list[dict[str, Any]]:
    rc, out, err = _run([str(WORKER_SH), "--json", "list", "--state", "queued"])
    if rc != 0:
        raise MCPToolError(f"worker.sh list failed (rc={rc}): {err.strip()}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as e:
        raise MCPToolError(f"worker.sh list produced unparseable JSON: {e}")
    # worker.sh list payload shape:
    #   { by_state: { queued: [task_summary, ...] }, total, ... }
    # Each task_summary is { id, kind, trust_level, target, ... } — enough
    # to filter on, but the full task body still needs `worker.sh show`.
    if isinstance(payload, dict) and isinstance(payload.get("by_state"), dict):
        by_state = payload["by_state"]
        queued = by_state.get("queued")
        if isinstance(queued, list):
            return queued
        return []
    raise MCPToolError(
        f"worker.sh list returned unexpected shape: top-level "
        f"{type(payload).__name__}, keys={sorted(payload.keys()) if isinstance(payload, dict) else 'n/a'}"
    )


def _load_full_task(task_id: str) -> dict[str, Any]:
    rc, out, err = _run([str(WORKER_SH), "--json", "show", task_id])
    if rc != 0:
        raise MCPToolError(f"worker.sh show {task_id} failed (rc={rc}): {err.strip()}")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as e:
        raise MCPToolError(f"worker.sh show produced unparseable JSON: {e}")
    if isinstance(payload, dict) and isinstance(payload.get("task"), dict):
        return payload["task"]
    if isinstance(payload, dict) and "id" in payload:
        return payload
    raise MCPToolError(f"worker.sh show returned unexpected shape: {type(payload).__name__}")


def _task_matches(task: dict[str, Any], filters: dict[str, Any]) -> bool:
    if filters.get("kind") and task.get("kind") != filters["kind"]:
        return False
    if filters.get("trust_level") and task.get("trust_level") != filters["trust_level"]:
        return False
    if filters.get("repo"):
        task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
        if task_input.get("repo") != filters["repo"]:
            return False
    return True


def handle_claim_task(args: dict[str, Any]) -> dict[str, Any] | None:
    adapter = args["adapter"]
    as_adapter_id = args["as_adapter_id"]
    worker_id = f"mcp:{as_adapter_id}"

    # List queued tasks, filter, pick first match.
    queued = _list_queued_tasks()
    candidate_id: str | None = None
    for t in queued:
        # The list shape from worker.sh may be summary; load the full task
        # to check filters reliably.
        tid = t.get("id") or t.get("task_id")
        if not tid:
            continue
        full = _load_full_task(tid)
        if _task_matches(full, args):
            candidate_id = tid
            break

    if candidate_id is None:
        return None

    rc, out, err = _run([
        str(WORKER_SH), "--json", "claim", candidate_id,
        "--adapter", adapter,
        "--worker", worker_id,
    ])
    if rc != 0:
        # Race may have lost the task between list and claim.
        raise MCPToolError(
            f"worker.sh claim {candidate_id} failed (rc={rc}): {err.strip() or out.strip()}"
        )
    # Return the (now claimed) task so the caller has its full body.
    return _load_full_task(candidate_id)


def handle_submit_result(args: dict[str, Any]) -> dict[str, Any]:
    task_id = args["task_id"]
    result = args["result"]
    as_adapter_id = args["as_adapter_id"]
    worker_id = f"mcp:{as_adapter_id}"

    # Worker.sh submit expects the result on disk; write to /tmp.
    tmp_path = Path("/tmp") / f"m92-mcp-{task_id}.result.json"
    tmp_path.write_text(json.dumps(result), encoding="utf-8")
    try:
        # Ensure the task is in running state (start if claimed). Worker.sh
        # submit accepts claimed or running; calling start is idempotent in
        # the sense that submit handles both states, so we skip start here
        # and let submit do the transition implicitly via state check.
        cmd = [
            str(WORKER_SH), "--json", "submit", task_id,
            "--worker", worker_id,
            "--result", str(tmp_path),
        ]
        if args.get("needs_approval"):
            cmd.append("--needs-approval")
        if args.get("fail"):
            cmd.append("--fail")
        rc, out, err = _run(cmd)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass

    if rc != 0:
        raise MCPToolError(
            f"worker.sh submit {task_id} failed (rc={rc}): {err.strip() or out.strip()}"
        )
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as e:
        raise MCPToolError(f"worker.sh submit produced unparseable JSON: {e}")
    # Surface the relevant fields per the M9.0 contract.
    return {
        "status": payload.get("status"),
        "appended_decision_ids": payload.get("appended_decision_ids", []),
        "task_id": task_id,
    }


HANDLERS = {
    "whoami": handle_whoami,
    "read_context": handle_read_context,
    "read_decisions": handle_read_decisions,
    "append_decision": handle_append_decision,
    "claim_task": handle_claim_task,
    "submit_result": handle_submit_result,
}


# ---------- dispatch ----------

def dispatch(name: str, args: dict[str, Any]) -> Any:
    """Validate arguments against the tool's inputSchema, then run handler.
    Returns the raw handler result. Caller (CLI or future MCP transport)
    decides how to wrap for wire format."""
    tool_def = _find_tool_def(name)
    if tool_def is None:
        raise MCPToolError(f"unknown tool: {name!r}")
    input_schema = tool_def.get("inputSchema") or {"type": "object"}
    if not isinstance(args, dict):
        raise MCPToolError(f"args must be a JSON object, got {type(args).__name__}")
    errors = _validate_against_schema(args, input_schema)
    if errors:
        raise MCPToolError(
            f"{name}: input validation failed: " + "; ".join(errors[:5])
        )
    handler = HANDLERS.get(name)
    if handler is None:
        raise MCPToolError(f"no handler registered for tool {name!r}")
    return handler(args)


# ---------- CLI ----------

def cmd_list_tools(_: argparse.Namespace) -> int:
    manifest = load_manifest()
    sys.stdout.write(json.dumps(manifest, indent=2) + "\n")
    return 0


def cmd_tool(args: argparse.Namespace) -> int:
    try:
        parsed_args = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as e:
        print(f"error: --args is not valid JSON: {e}", file=sys.stderr)
        return 1
    try:
        result = dispatch(args.name, parsed_args)
    except MCPToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0


# ---------- M13.0: JSON-RPC 2.0 over stdio (MCP server transport) ----------
#
# Additive on top of the M9.2 CLI surface. `list-tools` and `tool` keep
# their existing legacy contract verbatim; `serve` is the new entry point.
# The serve loop reuses dispatch() and load_manifest() unchanged — MCP-
# the-transport is a thin shim over the operational substance already in
# place since M9.2.
#
# Stdio discipline: every byte written to stdout is a JSON-RPC message.
# Any diagnostic / warning output goes to stderr. Tool subprocesses use
# capture_output=True (see _run), so their stdout cannot leak.

JSONRPC_VERSION = "2.0"
# Pinned to a stable published MCP protocol version. M13.0 shipped on
# 2024-11-05; bumped to 2025-06-18 in v0.3.0 to surface `structuredContent`
# + per-tool `outputSchema` so clients can consume parsed JSON directly
# instead of re-parsing a JSON string out of content[0].text. Back-compat
# preserved: the content[0].text shape is still emitted byte-identically.
MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "agent-continuity"

# JSON-RPC 2.0 standard error codes + the MCP-conventional server-error
# range. We use a single code (-32000) for handler exceptions; a real
# server could partition the range but the operator's M13.0 sign-off
# specified one code per failure class.
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_SERVER = -32000


class _InvalidParams(Exception):
    """Raised by an MCP method handler when the request params shape is
    wrong (distinct from MCPToolError, which means the tool's own
    inputSchema validation failed during dispatch)."""


def _ok_response(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}


def _err_response(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _read_substrate_version() -> str:
    try:
        return (REPO_ROOT / "core" / "VERSION").read_text().strip().splitlines()[0]
    except OSError:
        return "0.0.0"


def _mcp_initialize(_params: dict[str, Any]) -> dict[str, Any]:
    # Minimal-but-real per M13.0 sign-off. The empty `tools` object in
    # capabilities is the MCP-standard way of saying "this server exposes
    # tools" (the specifics come from tools/list).
    return {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "serverInfo": {
            "name": SERVER_NAME,
            "version": _read_substrate_version(),
        },
        "capabilities": {"tools": {}},
    }


def _mcp_tools_list(_params: dict[str, Any]) -> dict[str, Any]:
    manifest = load_manifest()
    tools = []
    for t in manifest.get("tools", []):
        entry = {
            "name": t["name"],
            "description": t.get("description", ""),
            "inputSchema": t.get("inputSchema", {"type": "object"}),
        }
        if "outputSchema" in t:
            entry["outputSchema"] = t["outputSchema"]
        tools.append(entry)
    return {"tools": tools}


def _wrap_structured(tool_name: str, result: Any) -> dict[str, Any]:
    """structuredContent MUST be a JSON object per MCP 2025-06-18.
    Handlers that return a dict pass through. Lists, scalars, and tools
    that can return None get a consistent envelope so the outputSchema
    is straightforward for clients to validate against.

    Envelope rules:
      claim_task -> {"task": <result>}        (handler returns Task or None;
                                               always wrapped so the schema
                                               is stable across both)
      dict       -> as-is                     (no envelope)
      list       -> {"entries": [...]}        (used by read_decisions)
      other      -> {"value": <result>}       (scalar / None guard)
    """
    if tool_name == "claim_task":
        return {"task": result}
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        return {"entries": result}
    return {"value": result}


def _mcp_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise _InvalidParams("params must be an object")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise _InvalidParams("params.name must be a non-empty string")
    arguments = params.get("arguments", {})
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _InvalidParams("params.arguments must be an object")

    try:
        result = dispatch(name, arguments)
    except MCPToolError as e:
        # The tool's own inputSchema / handler-level validation failed.
        # Surface as JSON-RPC invalid-params so clients see a structured
        # error rather than a server crash.
        raise _InvalidParams(str(e))

    # MCP 2025-06-18 tools/call response shape:
    #   content[0].text  — JSON string, byte-identical to the legacy
    #                      `mcp.sh tool <name>` CLI output. Back-compat
    #                      for clients that haven't migrated yet
    #                      (including this layer's internal smoke and
    #                      the local-shell transport).
    #   structuredContent — the same payload as a JSON object, ready
    #                       for direct consumption by modern MCP clients
    #                       without a re-parse step. Lists and scalars
    #                       are wrapped because the spec requires this
    #                       field to be an object.
    if result is None:
        text = "null"
    else:
        text = json.dumps(result, indent=2, ensure_ascii=False)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": _wrap_structured(name, result),
    }


# Methods that take params and return a JSON-RPC result.
MCP_METHODS = {
    "initialize": _mcp_initialize,
    "tools/list": _mcp_tools_list,
    "tools/call": _mcp_tools_call,
}

# Notifications: messages with no `id`. We accept them and do not respond.
# M13.0 only knows `notifications/initialized` (the standard MCP handshake
# completion notification). Any other notification is silently dropped —
# we don't error because, per JSON-RPC 2.0, a notification must never
# receive a response, including not an error response.


def _process_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process one parsed JSON-RPC message. Returns the response dict to
    send, or None when the message was a notification (no response is
    sent on the wire)."""
    is_notification = "id" not in msg
    req_id = msg.get("id")

    # JSON-RPC 2.0: requests with an id but no method are invalid
    # requests; notifications without a method we silently drop (we
    # cannot respond to a notification by spec).
    method = msg.get("method")
    if not isinstance(method, str) or not method:
        if is_notification:
            return None
        return _err_response(req_id, ERR_INVALID_REQUEST, "missing or invalid 'method'")

    if is_notification:
        # Notifications never produce a response, even for unknown
        # methods. The operator's sign-off is explicit on this.
        return None

    handler = MCP_METHODS.get(method)
    if handler is None:
        return _err_response(req_id, ERR_METHOD_NOT_FOUND, f"method not found: {method!r}")

    raw_params = msg.get("params", {})
    if raw_params is None:
        raw_params = {}
    if not isinstance(raw_params, dict):
        return _err_response(req_id, ERR_INVALID_PARAMS, "params must be a JSON object")

    try:
        result = handler(raw_params)
    except _InvalidParams as e:
        return _err_response(req_id, ERR_INVALID_PARAMS, str(e))
    except Exception as e:  # noqa: BLE001 — surface any handler crash
        # A handler exception is a server-side error, not a protocol
        # error. We don't tear down the server; the client gets a -32000
        # for this request and may continue with subsequent calls.
        return _err_response(req_id, ERR_SERVER, f"handler exception: {type(e).__name__}: {e}")

    return _ok_response(req_id, result)


def _serve_loop(stdin, stdout, stderr) -> int:
    """Read newline-delimited JSON-RPC messages from stdin until EOF.
    Writes responses to stdout (one JSON object per line). Diagnostics
    go to stderr. Exits 0 on clean EOF."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            # JSON-RPC 2.0 §5: on parse error the id MUST be null since
            # we cannot reliably extract one from malformed input.
            resp = _err_response(None, ERR_PARSE, f"parse error: {e}")
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
            continue
        if not isinstance(msg, dict):
            resp = _err_response(None, ERR_INVALID_REQUEST, "request must be a JSON object")
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
            continue
        resp = _process_message(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
    return 0


def cmd_serve(_args: argparse.Namespace) -> int:
    return _serve_loop(sys.stdin, sys.stdout, sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M9.2 MCP tool surface + M13.0 stdio server for the six adapter-"
            "contract operations. See docs/m9-adapter-pattern.md for the "
            "contract semantics."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-tools", help="print the tool manifest").set_defaults(func=cmd_list_tools)

    t = sub.add_parser("tool", help="invoke a tool by name (CLI dispatch)")
    t.add_argument("name", help="tool name (one of: whoami, read_context, read_decisions, append_decision, claim_task, submit_result)")
    t.add_argument("--args", default="{}", help="JSON object with tool arguments (default: {})")
    t.set_defaults(func=cmd_tool)

    sub.add_parser(
        "serve",
        help="run as a JSON-RPC 2.0 MCP server on stdio (M13.0)",
    ).set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
