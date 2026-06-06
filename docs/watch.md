# Opt-in agent-home watcher (M9.4)

`agent-continuity watch` is the substrate's **opt-in** background watcher that auto-wires new AI-agent homes (Codex, Cursor, Gemini CLI, etc.) the moment they appear on disk. Off by default. The substrate's identity stays "tool" — not "background service" — unless you explicitly enable this.

## Why it exists

Before M9.4, the wiring lifecycle was: install agent-continuity once via bootstrap, wire whatever agents existed at that moment, and then **remember to re-run `connect all --apply` every time you installed a new AI agent**.

Real-world failure mode: you install Codex at 2pm while focused on something else, forget to wire it, and your Codex sessions can't see the substrate. Hours or days later, you wonder why your decision log is empty for Codex-authored decisions.

The watcher fixes that. Once enabled, you install Codex (or Cursor, or Gemini CLI, or any future supported agent) and within seconds the wiring runs automatically.

## What it is

A user-scope macOS LaunchAgent at:

```
~/Library/LaunchAgents/com.agent-continuity.watcher.plist
```

It uses `WatchPaths` (fsevents-driven, not polling) on `$HOME` and `$HOME/Library/Application Support`. When launchd wakes the watcher, it runs `agent-continuity watch --tick`, which:

1. Calls `agent-continuity connect doctor --json` to detect drift (any wireable target whose home directory exists but config isn't current)
2. Runs `agent-continuity connect <target> --apply` for each drifted target
3. Appends the result to the audit log

Runs as the **user**, not root. Not a `LaunchDaemon`. No privilege escalation, ever.

## Enable

Two equivalent paths.

**Via the bootstrap one-liner** (cleanest for fresh installs):

```bash
curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh \
  | bash -s -- --connect-all --watch --upgrade
```

**Via the CLI after install**:

```bash
agent-continuity watch enable
```

Both paths do the same thing:

1. Write the plist
2. `launchctl load` it
3. Run one immediate sync (so state is consistent at the moment of enable)
4. Print where the audit log lives

## Disable

```bash
agent-continuity watch disable
```

Unloads the LaunchAgent and removes the plist. Idempotent — safe to run when not enabled.

## Status

```bash
agent-continuity watch status            # human-readable
agent-continuity watch status --json     # machine-readable
```

Shows: enabled/disabled, last tick, last apply, success/failure counters since enable.

Example:

```
watcher:               ENABLED
  launchagent_path:    /Users/mau/Library/LaunchAgents/com.agent-continuity.watcher.plist
  launchagent_present: True
  launchctl_loaded:    True
  log_path:            /Users/mau/Library/Logs/agent-continuity/watcher.log
  last_tick:           2026-06-06T13:42:18Z
  last_apply:          2026-06-06T13:42:18Z
  last_apply_ok/fail:  1 / 0
  total_apply_ok/fail: 3 / 0
```

## Audit trail

Every action the watcher takes is appended (with UTC timestamp) to:

```
~/Library/Logs/agent-continuity/watcher.log
```

Log rotates at 1 MB to `watcher.log.1`. The watcher never silently auto-wires anything you can't find in this file later.

Example log line after a Codex install:

```
[2026-06-06T14:20:31Z] tick: drift detected: ['codex-skill']
[2026-06-06T14:20:31Z] apply: target=codex-skill wired ok
```

## Privacy and trust posture

| Property | Status |
|---|---|
| Runs as root | No — user-scope LaunchAgent only |
| Phones home | No |
| Reads files outside substrate dirs | No — only invokes `connect doctor` / `connect <target> --apply` |
| Auto-updates the substrate binary | No — the watcher uses whichever binary is at `~/.local/bin/agent-continuity` |
| Writes to your shell rc / dotfiles | No |
| Modifies files outside what `connect --apply` already does | No |
| Audit trail | Yes — every action appended to `~/Library/Logs/agent-continuity/watcher.log` |

The watcher is a thin LaunchAgent that calls existing operator commands on a schedule. It cannot do anything you couldn't do by hand with `connect all --apply`.

## macOS TCC permission prompt

The first time the watcher fires on a TCC-protected directory (e.g. `~/Library/Application Support/`), macOS may show a one-time permission prompt:

> "agent-continuity" wants to access files in your "Application Support" folder. [Don't Allow] [OK]

This is **macOS asking you to authorize the substrate**, not the substrate asking for anything beyond what `connect` already does. Grant it for the watcher to work. The substrate has no control over this prompt — it's how macOS handles File and Folder Access.

If you deny it, the watcher will still load but won't detect changes inside protected directories. You can re-grant via System Settings → Privacy & Security → Files and Folders.

## What it does NOT do

- It doesn't upgrade the substrate binary. Upgrading `agent-continuity` itself is still an explicit `bootstrap --upgrade` action.
- It doesn't unwire agents whose home directory disappears (uninstalled agent). That's a separate `connect <target> --remove` slice if needed.
- It doesn't watch for upstream config changes inside an agent's settings file. Only the presence of the agent home directory triggers detection.
- It doesn't run on Linux yet. Linux/systemd user-unit parity is a follow-up slice; the `--tick` logic is platform-agnostic and ready for that work.

## When to enable vs. not enable

**Enable if**: you're the primary operator of one machine and install AI agents over time. The set-and-forget UX wins.

**Don't enable if**: you want strict "no background processes, every config touch is operator-initiated" posture (security-sensitive environments, shared/managed machines). The default-off bootstrap respects that — you'll re-run `connect all --apply` manually after installing each new agent.

The substrate identity is honest about both modes. The README documents them both.
