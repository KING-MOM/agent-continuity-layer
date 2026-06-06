#!/usr/bin/env python3
"""M13.0 smoke client for the MCP stdio server.

Spawns `scripts/mcp.sh serve` as a subprocess and drives it through a
JSON-RPC 2.0 conversation using only stdlib. Exercises:

  - initialize (shape: protocolVersion, serverInfo, capabilities.tools)
  - notifications/initialized (must NOT produce a response)
  - tools/list (must list the six M9.2 tools)
  - tools/call for each of the six tools — at minimum verifies the
    MCP-shape response is returned. For read-only tools (whoami,
    read_context, read_decisions, claim_task on an empty queue) the
    decoded payload is also verified.
  - Error paths: parse error (-32700) on malformed JSON, method-not-
    found (-32601) on unknown method, invalid params (-32602) on
    tools/call with a missing tool name.
  - Clean exit (rc=0) on stdin EOF within timeout.

Runs against a sandboxed XDG environment so it does not write to the
real continuity namespace.

Exit code: 0 if all checks pass; 1 on any failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_SH = REPO_ROOT / "scripts" / "mcp.sh"

# Tool names exposed by the M9.2 manifest. Hard-coded here so a regression
# that drops one from the manifest is caught instead of silently passing.
EXPECTED_TOOLS = {
    "whoami",
    "read_context",
    "read_decisions",
    "append_decision",
    "claim_task",
    "submit_result",
}


class SmokeError(Exception):
    pass


class _Client:
    """Newline-delimited JSON-RPC client over the server's stdin/stdout."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._next_id = 1

    def send_request(self, method: str, params: dict | None = None) -> dict:
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return self._read_response(rid)

    def send_notification(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def send_raw(self, text: str) -> dict:
        """Send a raw line (used to test parse-error path). Returns the
        first response on stdout, which for parse errors will have
        id=null per JSON-RPC 2.0."""
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()
        return self._read_one()

    def _read_one(self) -> dict:
        line = self.proc.stdout.readline()
        if not line:
            raise SmokeError("server closed stdout unexpectedly")
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise SmokeError(f"server emitted non-JSON on stdout: {line!r} ({e})")

    def _read_response(self, expected_id: int) -> dict:
        resp = self._read_one()
        if resp.get("id") != expected_id:
            raise SmokeError(
                f"id mismatch: expected {expected_id}, got {resp.get('id')!r}; full response: {resp}"
            )
        return resp

    def assert_quiet_for(self, seconds: float) -> None:
        """Verify the server produces no output for `seconds` after a
        notification has been sent. Used to prove notifications/initialized
        does not get a response."""
        # We can't reliably do non-blocking reads on a pipe without
        # platform tricks. Strategy: send a SUBSEQUENT request and confirm
        # the response is to THAT request, not a phantom response to the
        # notification. If the server had wrongly responded to the
        # notification, we'd see TWO lines and the id would be wrong.
        # The pattern is implemented in the caller.
        time.sleep(seconds)  # currently informational; real check is in caller


# ─────────────────────────────────────────────────────────────────────────
# Sandbox setup

def _make_sandbox() -> Path:
    base = Path(tempfile.mkdtemp(prefix="mcp-smoke."))
    for sub in ("config", "state", "cache", "data"):
        (base / sub / "agent-continuity").mkdir(parents=True, exist_ok=True)
    return base


def _sandbox_env(base: Path) -> dict:
    env = os.environ.copy()
    # Wipe HOME just in case; rely entirely on XDG vars below.
    env["HOME"] = str(base / "home")
    (base / "home").mkdir(exist_ok=True)
    env["XDG_CONFIG_HOME"] = str(base / "config")
    env["XDG_STATE_HOME"] = str(base / "state")
    env["XDG_CACHE_HOME"] = str(base / "cache")
    env["XDG_DATA_HOME"] = str(base / "data")
    return env


# ─────────────────────────────────────────────────────────────────────────
# Test runner

class _Runner:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def check(self, name: str, fn) -> None:
        print(f"── {name} ──")
        try:
            fn()
        except SmokeError as e:
            print(f"   FAIL: {e}")
            self.failed.append((name, str(e)))
        except Exception as e:  # noqa: BLE001
            print(f"   FAIL: {type(e).__name__}: {e}")
            self.failed.append((name, f"{type(e).__name__}: {e}"))
        else:
            print("   PASS")
            self.passed.append(name)


# ─────────────────────────────────────────────────────────────────────────
# Individual checks. Each receives the live client and asserts something.

def _expect_ok(resp: dict, context: str) -> dict:
    if "error" in resp:
        raise SmokeError(f"{context}: expected success, got error {resp['error']}")
    if "result" not in resp:
        raise SmokeError(f"{context}: response has neither result nor error: {resp}")
    return resp["result"]


def _expect_error(resp: dict, code: int, context: str) -> dict:
    if "error" not in resp:
        raise SmokeError(f"{context}: expected error code {code}, got success {resp.get('result')}")
    if resp["error"].get("code") != code:
        raise SmokeError(
            f"{context}: expected error code {code}, got {resp['error']}"
        )
    return resp["error"]


def check_initialize(c: _Client) -> None:
    resp = c.send_request("initialize", {"protocolVersion": "2025-06-18"})
    result = _expect_ok(resp, "initialize")
    if "protocolVersion" not in result:
        raise SmokeError(f"initialize missing protocolVersion: {result}")
    # Server pins to 2025-06-18 (v0.3.0 bump for structuredContent + outputSchema).
    if result["protocolVersion"] != "2025-06-18":
        raise SmokeError(
            f"initialize protocolVersion mismatch: expected 2025-06-18, "
            f"got {result['protocolVersion']!r}"
        )
    if "serverInfo" not in result or "name" not in result["serverInfo"]:
        raise SmokeError(f"initialize missing serverInfo.name: {result}")
    if "capabilities" not in result or "tools" not in result["capabilities"]:
        raise SmokeError(f"initialize missing capabilities.tools: {result}")


def check_initialized_notification_no_response(c: _Client) -> None:
    # Send the notification (no id → server must not respond).
    c.send_notification("notifications/initialized")
    # Brief pause to let any (incorrect) response queue up on the pipe.
    time.sleep(0.1)
    # Send a real request RIGHT AFTER and verify the response is to THIS
    # request, not to the notification. If the server had wrongly
    # responded to the notification, we would either see two lines on
    # stdout or an id mismatch on the next response.
    resp = c.send_request("initialize")
    _expect_ok(resp, "post-notification initialize")
    # If a phantom notification response had been written, the second
    # read for THIS request would have caught it as id=None or no id —
    # which _read_response would detect via the id-mismatch check.


def check_tools_list(c: _Client) -> None:
    resp = c.send_request("tools/list")
    result = _expect_ok(resp, "tools/list")
    if "tools" not in result or not isinstance(result["tools"], list):
        raise SmokeError(f"tools/list missing tools array: {result}")
    names = {t.get("name") for t in result["tools"]}
    missing = EXPECTED_TOOLS - names
    extra = names - EXPECTED_TOOLS
    if missing:
        raise SmokeError(f"tools/list missing tools: {sorted(missing)}")
    if extra:
        raise SmokeError(f"tools/list has unexpected tools: {sorted(extra)}")
    # Every tool must have an inputSchema (M9.2 contract).
    # Every tool must have an outputSchema (MCP 2025-06-18 contract).
    for t in result["tools"]:
        if "inputSchema" not in t:
            raise SmokeError(f"tool {t.get('name')} missing inputSchema in tools/list")
        if "outputSchema" not in t:
            raise SmokeError(f"tool {t.get('name')} missing outputSchema in tools/list")
        os_ = t["outputSchema"]
        if not isinstance(os_, dict) or os_.get("type") != "object":
            raise SmokeError(
                f"tool {t.get('name')} outputSchema must declare type:object "
                f"(structuredContent requires object), got {os_!r}"
            )


def _call_tool(c: _Client, name: str, arguments: dict) -> dict:
    """Invoke tools/call and verify the MCP envelope shape.

    Verifies both:
      - Back-compat: content[0].type=='text' with a JSON string in .text
      - MCP 2025-06-18: structuredContent is a JSON object (always present
        on successful calls, regardless of whether the handler returned
        dict / list / None).
    """
    resp = c.send_request("tools/call", {"name": name, "arguments": arguments})
    if "error" in resp:
        return resp
    result = _expect_ok(resp, f"tools/call {name}")
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise SmokeError(f"tools/call {name}: result missing 'content' list: {result}")
    first = content[0]
    if first.get("type") != "text":
        raise SmokeError(f"tools/call {name}: content[0].type != 'text': {first}")
    if not isinstance(first.get("text"), str):
        raise SmokeError(f"tools/call {name}: content[0].text not a string: {first}")
    # MCP 2025-06-18: structuredContent must be a JSON object.
    if "structuredContent" not in result:
        raise SmokeError(f"tools/call {name}: missing structuredContent: {result}")
    sc = result["structuredContent"]
    if not isinstance(sc, dict):
        raise SmokeError(
            f"tools/call {name}: structuredContent not an object "
            f"(MCP 2025-06-18 requires object): {type(sc).__name__}"
        )
    return resp


def check_tool_whoami(c: _Client) -> None:
    resp = _call_tool(c, "whoami", {})
    payload = json.loads(resp["result"]["content"][0]["text"])
    if payload.get("adapter_id") != "continuity-layer-local":
        raise SmokeError(f"whoami returned unexpected adapter_id: {payload}")
    # MCP 2025-06-18: structuredContent must equal the parsed text payload
    # for dict-returning tools (no envelope).
    sc = resp["result"]["structuredContent"]
    if sc != payload:
        raise SmokeError(f"whoami structuredContent != parsed text: {sc} vs {payload}")


def check_tool_read_context(c: _Client) -> None:
    resp = _call_tool(c, "read_context", {"format": "json"})
    if "error" in resp:
        # context-snapshot may not be present in some sandboxes; accept
        # the documented error pathway if so.
        raise SmokeError(f"read_context unexpectedly errored: {resp['error']}")
    payload = json.loads(resp["result"]["content"][0]["text"])
    if "schema_version" not in payload:
        raise SmokeError(f"read_context payload missing schema_version: {payload}")
    sc = resp["result"]["structuredContent"]
    if sc != payload:
        raise SmokeError(f"read_context structuredContent != parsed text")


def check_tool_read_decisions(c: _Client) -> None:
    # In the sandbox there is no decisions.jsonl, so the handler returns
    # an empty list. We accept either an empty list or a list with
    # entries (the latter would only happen if some path leaks).
    resp = _call_tool(c, "read_decisions", {})
    if "error" in resp:
        raise SmokeError(f"read_decisions errored: {resp['error']}")
    payload = json.loads(resp["result"]["content"][0]["text"])
    if not isinstance(payload, list):
        raise SmokeError(f"read_decisions did not return a list: {payload!r}")
    # structuredContent wraps the list as {entries: [...]}
    sc = resp["result"]["structuredContent"]
    if "entries" not in sc or not isinstance(sc["entries"], list):
        raise SmokeError(f"read_decisions structuredContent missing entries list: {sc}")
    if sc["entries"] != payload:
        raise SmokeError(f"read_decisions structuredContent.entries != parsed text")


def check_tool_claim_task(c: _Client) -> None:
    # Empty sandbox queue → handler returns None → MCP text is the JSON
    # literal "null". Either way, the envelope must be correct.
    resp = _call_tool(
        c,
        "claim_task",
        {"adapter": "codex", "as_adapter_id": "smoke"},
    )
    # Accept either a successful null payload or a server-side error
    # for missing queue dir (depending on handler tolerance).
    if "error" in resp:
        if resp["error"]["code"] != -32000:
            raise SmokeError(
                f"claim_task error has unexpected code: {resp['error']}"
            )
        return
    text = resp["result"]["content"][0]["text"]
    if text != "null" and not text.startswith("{"):
        raise SmokeError(f"claim_task returned unexpected payload: {text!r}")
    # structuredContent always wraps claim_task as {task: <obj or null>}
    sc = resp["result"]["structuredContent"]
    if "task" not in sc:
        raise SmokeError(f"claim_task structuredContent missing 'task' key: {sc}")
    if text == "null" and sc["task"] is not None:
        raise SmokeError(f"claim_task text=null but structuredContent.task != null: {sc}")


def check_tool_append_decision_error_path(c: _Client) -> None:
    # Call with insufficient args to provoke the dispatch-level validation
    # error. The point is to validate that the SERVER correctly maps an
    # MCPToolError to JSON-RPC -32602 (invalid params), NOT to test the
    # handler's own validation logic.
    resp = c.send_request("tools/call", {"name": "append_decision", "arguments": {}})
    _expect_error(resp, -32602, "append_decision with empty args")


def check_tool_submit_result_error_path(c: _Client) -> None:
    # Same idea: empty args fail schema validation → -32602.
    resp = c.send_request("tools/call", {"name": "submit_result", "arguments": {}})
    _expect_error(resp, -32602, "submit_result with empty args")


def check_method_not_found(c: _Client) -> None:
    resp = c.send_request("foo/bar", {})
    _expect_error(resp, -32601, "unknown method foo/bar")


def check_invalid_params_for_tools_call(c: _Client) -> None:
    # tools/call without a name field → invalid params.
    resp = c.send_request("tools/call", {"arguments": {}})
    _expect_error(resp, -32602, "tools/call missing name")


def check_parse_error(c: _Client) -> None:
    resp = c.send_raw("{ this is not valid json")
    if resp.get("id") is not None:
        raise SmokeError(f"parse error response must have id=null per JSON-RPC 2.0; got {resp}")
    _expect_error(resp, -32700, "parse error")


def check_clean_eof_exit(proc: subprocess.Popen) -> None:
    # Close stdin → server should drain and exit 0 quickly.
    proc.stdin.close()
    try:
        rc = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise SmokeError("server did not exit within 5s of stdin close")
    if rc != 0:
        # Capture stderr for diagnostic.
        err = ""
        try:
            err = proc.stderr.read() or ""
        except Exception:  # noqa: BLE001
            pass
        raise SmokeError(f"server exited with rc={rc}; stderr: {err[:500]}")


# ─────────────────────────────────────────────────────────────────────────
# Main

def main() -> int:
    if not MCP_SH.exists():
        print(f"error: {MCP_SH} not found", file=sys.stderr)
        return 1

    sandbox = _make_sandbox()
    env = _sandbox_env(sandbox)
    print(f"sandbox: {sandbox}")
    print(f"server:  {MCP_SH} serve")

    proc = subprocess.Popen(
        [str(MCP_SH), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        bufsize=1,
    )
    client = _Client(proc)
    runner = _Runner()

    try:
        runner.check("initialize: shape (protocolVersion, serverInfo, capabilities.tools)", lambda: check_initialize(client))
        runner.check("notifications/initialized produces no response", lambda: check_initialized_notification_no_response(client))
        runner.check("tools/list returns the six M9.2 tools", lambda: check_tools_list(client))
        runner.check("tools/call whoami", lambda: check_tool_whoami(client))
        runner.check("tools/call read_context", lambda: check_tool_read_context(client))
        runner.check("tools/call read_decisions", lambda: check_tool_read_decisions(client))
        runner.check("tools/call claim_task (empty queue)", lambda: check_tool_claim_task(client))
        runner.check("tools/call append_decision (invalid args → -32602)", lambda: check_tool_append_decision_error_path(client))
        runner.check("tools/call submit_result (invalid args → -32602)", lambda: check_tool_submit_result_error_path(client))
        runner.check("unknown method → -32601", lambda: check_method_not_found(client))
        runner.check("tools/call with missing name → -32602", lambda: check_invalid_params_for_tools_call(client))
        runner.check("malformed JSON line → -32700 with id=null", lambda: check_parse_error(client))
        runner.check("stdin EOF → server exits 0", lambda: check_clean_eof_exit(proc))
    finally:
        if proc.poll() is None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    total = len(runner.passed) + len(runner.failed)
    print()
    print("════════════════════════════════════════════════════")
    print(f"mcp-serve smoke: {len(runner.passed)}/{total} passed, {len(runner.failed)} failed")
    for name in runner.passed:
        print(f"  PASS  {name}")
    for name, msg in runner.failed:
        print(f"  FAIL  {name}  —  {msg}")
    print(f"sandbox: {sandbox}")

    if runner.failed:
        return 1
    # Clean sandbox on success only (mirrors release-smoke.sh).
    import shutil
    shutil.rmtree(sandbox, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
