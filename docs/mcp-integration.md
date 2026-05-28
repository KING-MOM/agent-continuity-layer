# MCP Integration

How to connect an MCP-aware client to the `agent-continuity` substrate.

The substrate ships a JSON-RPC 2.0 server over stdio (M13.0). The command is the same for every client:

```bash
agent-continuity mcp serve
```

It exposes the six adapter-contract tools — `whoami`, `read_context`, `read_decisions`, `append_decision`, `claim_task`, `submit_result` — declared in `core/mcp/tools.json`. The same tools that the legacy `agent-continuity mcp tool <name>` CLI dispatch path exposes; this slice just wraps them in real MCP wire protocol.

---

## Prerequisites

```bash
agent-continuity --version
agent-continuity doctor --human
```

Both must succeed. The `doctor` line must show `substrate v<X.Y.Z>` under `[OK   ] repo`. If either fails, see [Troubleshooting](#troubleshooting).

The MCP server inherits the environment of the MCP client that launches it (XDG paths, `HOME`, etc.). It writes only to the same continuity namespace the CLI writes to.

---

## Client configurations

In each snippet below, replace `agent-continuity` with the absolute path returned by `which agent-continuity` if the binary is not on your client's `PATH`. GUI MCP clients (Claude Desktop, Cursor, Zed) often inherit a different `PATH` than your shell — when in doubt, hard-code the absolute path.

### Claude Desktop

Edit `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "agent-continuity": {
      "command": "agent-continuity",
      "args": ["mcp", "serve"]
    }
  }
}
```

Restart Claude Desktop. The six tools appear under the hammer icon.

### Cursor

Edit either:

- Project-scoped: `.cursor/mcp.json` (committed with the project)
- Global: `~/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "agent-continuity": {
      "type": "stdio",
      "command": "agent-continuity",
      "args": ["mcp", "serve"]
    }
  }
}
```

The `"type": "stdio"` discriminator is required by Cursor — without it the server is not launched.

### Zed

Edit `settings.json`:

- macOS: `~/.zed/settings.json`
- Linux: `$XDG_CONFIG_HOME/zed/settings.json` (defaults to `~/.config/zed/settings.json`)

```json
{
  "context_servers": {
    "agent-continuity": {
      "command": "agent-continuity",
      "args": ["mcp", "serve"]
    }
  }
}
```

Zed's key is `context_servers`, not `mcpServers` — they kept the term they used before MCP standardized. The Zed UI's `Agent Panel → Settings → Add Custom Server` writes to this same key.

### MCP Inspector (generic stdio test client)

Anthropic ships a reference inspector that speaks stdio MCP and gives you a UI:

```bash
npx @modelcontextprotocol/inspector agent-continuity mcp serve
```

Opens a local UI on port 6274. Useful for poking at the server interactively and seeing every request/response on the wire.

### Manual stdio smoke (no client needed)

A one-liner that proves the server is reachable:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize"}' | agent-continuity mcp serve
```

Expected response (single line on the wire; formatted here for readability):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "serverInfo": {"name": "agent-continuity", "version": "<your-installed-version>"},
    "capabilities": {"tools": {}}
  }
}
```

`version` reflects whatever `core/VERSION` shipped in your installed tarball. The `protocolVersion` field is pinned to the MCP spec version this substrate implements; it does not track the substrate's own version.

For a full conversation, the installed substrate also ships a self-test client:

```bash
python3 "$(readlink "$(which agent-continuity)" | xargs dirname | xargs dirname)/scripts/_mcp_serve_smoke.py"
```

It exercises all six tools plus the protocol/error edge cases in a sandboxed XDG environment. Exits `0` on success.

---

## Stdio discipline (important)

The server uses stdio as the wire transport. This means **every byte the server writes to stdout is a JSON-RPC message**. Diagnostics, warnings, and debug output go to stderr.

Practical consequence: if you wrap `agent-continuity mcp serve` in a shell script, helper wrapper, or systemd unit, do not let your wrapper print anything to stdout. A stray `echo "starting server..."` in the launcher will be parsed by the MCP client as malformed JSON-RPC.

For the same reason: never tee stdout for logging. Tee stderr instead, or use a structured logging shim that writes to a file:

```bash
agent-continuity mcp serve 2> ~/.cache/agent-continuity/mcp-server.log
```

---

## Troubleshooting

### `command not found: agent-continuity`

The install puts the binary at `$XDG_BIN_HOME/agent-continuity` (defaults to `~/.local/bin/agent-continuity`). GUI MCP clients (Claude Desktop, Cursor, Zed) frequently launch with a stripped `PATH` that does not include `~/.local/bin`.

Two fixes:

1. Put the binary on the launcher's PATH — for macOS app bundles this usually means adding it to `/usr/local/bin` or `/opt/homebrew/bin`.
2. Use the absolute path in the config:

   ```json
   "command": "/Users/<you>/.local/bin/agent-continuity"
   ```

Verify with `which agent-continuity` in your shell, then check whether that path is reachable from the client.

### `agent-continuity doctor` reports `[ERROR]`

Don't connect a broken install to an MCP client — the tools will surface confusing handler errors. Fix the install first:

```bash
agent-continuity doctor --human
```

Common causes:

- `core/VERSION` missing → install is incomplete; reinstall from a fresh tarball
- `scripts/*.sh` not executable → tarball was extracted with restricted permissions; `chmod +x scripts/*.sh bin/agent-continuity` inside the install dir
- Active symlink dangling → the version dir was deleted underneath `active`; re-run `install.sh --from-tarball` to recreate

### Working directory confusion

The server resolves the substrate dir from the symlink chain on the binary's location, not from `cwd`. The MCP client can launch it from any working directory. If `read_context` returns an unexpected snapshot or an error, the cause is almost never the cwd — check the resolved `XDG_STATE_HOME` and `XDG_CONFIG_HOME` (the server inherits whatever the client launched it with).

To inspect the resolved environment:

```bash
agent-continuity doctor --human | grep -E "(state|config|cache)"
```

### Tool call returns JSON-RPC error `-32602` (invalid params)

The tool's `inputSchema` rejected your arguments. Check the schema:

```bash
agent-continuity mcp list-tools | python3 -m json.tool | grep -A 20 '"name": "<tool>"'
```

Every tool listed by `tools/list` over MCP has the same `inputSchema` shown here.

### Tool call returns JSON-RPC error `-32000` (handler exception)

A tool's underlying script crashed (e.g., a permissions issue, missing trust policy). The error message field includes the exception type. Drop to the CLI to reproduce outside MCP:

```bash
agent-continuity mcp tool <name> --args '<args-json>'
```

The CLI surfaces the same error directly, with the underlying script's stderr available for diagnosis.

### Server output appears on stdout in non-JSON form

If your MCP client logs "parse error" or rejects messages, something other than the server is writing to stdout. Common culprits:

- A shell startup file (`~/.bashrc`, `~/.zshrc`) that `echo`s a greeting when a non-interactive shell starts
- A wrapper script that prints "starting..." before `exec`-ing the server
- A version manager (`asdf`, `nvm`, `rbenv`) emitting initialization output to stdout

Diagnose by running:

```bash
agent-continuity mcp serve < /dev/null
```

The server should print nothing to stdout in that scenario (no requests means no responses). If anything appears, it is not coming from the server.

---

## What's exposed

The six tools, with one-line semantics. See `docs/m9-adapter-pattern.md` for the full contract.

| Tool | Reads | Writes |
|------|-------|--------|
| `whoami` | substrate identity | — |
| `read_context` | `core/context-snapshot.{json,md}` | — |
| `read_decisions` | `$XDG_STATE_HOME/agent-continuity/decisions.jsonl` | — |
| `append_decision` | trust policy | decisions log |
| `claim_task` | task queue | claim record |
| `submit_result` | claim record | task result, decisions log |

The MCP transport is purely a wire format around these tools. The CLI dispatch path (`agent-continuity mcp tool <name> --args '...'`) and the stdio server return byte-identical payloads for the same arguments — pick whichever transport your agent already speaks.
