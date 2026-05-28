# Connect All Adapters

Install gives you the substrate. `connect` points local adapter hosts at it.

The intended first-run shape is:

```bash
agent-continuity doctor --human
agent-continuity connect doctor
agent-continuity connect all --apply
agent-continuity connect doctor
```

`connect doctor` is a dry-run health report. It tells you which local hosts are
already connected and which config files would be written.

`connect all --apply` writes the local configuration needed for supported hosts
to use the same continuity substrate.

## What It Connects

| Target | What gets configured |
|---|---|
| Claude Desktop | `mcpServers.agent-continuity` → `agent-continuity mcp serve` |
| Cursor | `mcpServers.agent-continuity` with `type: stdio` |
| Zed | `context_servers.agent-continuity` → `agent-continuity mcp serve` |
| Claude/Codex/OpenClaw skills | Thin `SKILL.md` pointers installed through `install-thin-skills.sh` |
| OpenClaw bridge | Reported only. The bridge is host-side and is not rewritten by `connect`. |

The result is one local command surface shared by Claude Desktop, Cursor, Zed,
Codex, Claude, OpenClaw/Mika, shell, MCP, bundle, and Python SDK paths.

## Safety

- Dry-run by default.
- `--apply` is required before writing.
- Existing MCP config files are backed up before overwrite.
- Thin skill installs reuse the existing backup/downgrade/symlink guards.
- OpenClaw bridge files are inspected, not rewritten.
- Trust policy is not broadened by `connect`.
- No VM sync is configured by `connect`.

## Commands

```bash
agent-continuity connect doctor
agent-continuity connect all --apply
agent-continuity connect mcp --apply
agent-continuity connect skills --apply
agent-continuity connect claude-desktop --apply
agent-continuity connect cursor --apply
agent-continuity connect zed --apply
agent-continuity connect claude --apply
agent-continuity connect codex --apply
agent-continuity connect openclaw-skill --apply
agent-continuity connect openclaw
```

Use `--json` for automation:

```bash
agent-continuity connect doctor --json
```

Roll-ups:

- `all` = MCP hosts + thin skills + OpenClaw bridge status.
- `mcp` = `claude-desktop`, `cursor`, `zed`.
- `skills` = `claude`, `codex`, `openclaw-skill`.
- `openclaw` = bridge status only; no host-side bridge files are written.

## Why This Exists

Manual adapter setup was the last big gap between "installed" and "usable".
Before `connect`, the pieces existed but each host had to be wired by hand.
After `connect`, a user can install once and connect all local adapter surfaces in
one operator-mediated step.
