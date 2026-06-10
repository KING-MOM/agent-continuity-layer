# Git-Backed Continuity Memory

Git-backed memory is the low-risk cross-device path for durable continuity.
It stores curated memory records in a private Git repository instead of
symlinking live runtime folders or putting memory directly into a cloud
database.

## Recommended Shape

- Git owns code, schemas, docs, skills, migrations, and curated memory.
- Host-local state owns raw sessions, caches, credentials, queues, and
  machine-local trust policy.
- A future realtime backend may own live task queues and device presence.
- Bundle handoff remains the fallback for web agents or untrusted devices.

## Scope Versus `agent-continuity sync`

There are two sync surfaces:

- `agent-continuity sync` is the M10 device/VM sync path for continuity
  artifacts in environments that use that backend.
- `agent-continuity git-memory ...` is the Git-backed source-of-truth path for
  operators who want a private repo to carry durable memory across laptops,
  desktops, and VMs.

Use Git-backed memory when you want GitHub (or another private Git remote) to
answer "which machine has the latest memory?" Use M10 sync when your deployment
already has the continuity VM/backend in the trust boundary.

## What Goes Into Memory Git

- `projects/` — registered projects and related repo identities.
- `contexts/` — compact project context snapshots and hand-curated summaries.
- `decisions/` — append-only decision logs.
- `handoffs/` — durable handoff/result summaries.
- `artifacts/` — artifact indexes, not secret-bearing blobs.
- `devices/` — descriptive device identities.
- `metadata/` — export manifests and sync notes.

## What Must Stay Out

- Credentials, OAuth tokens, API keys, service account files.
- e.firma material, SAT certificates, signing keys, passwords.
- Raw Claude/Codex/OpenClaw session dumps.
- Browser profiles, cookies, cache directories.
- Worker queue spools and locks.
- Machine-local trust grants.

## Commands

```bash
agent-continuity git-memory --path /path/to/agent-memory init
agent-continuity git-memory --path /path/to/agent-memory export
agent-continuity git-memory --path /path/to/agent-memory sync
agent-continuity git-memory --path /path/to/agent-memory status
```

After export, review the diff before committing:

```bash
git -C /path/to/agent-memory status --short
git -C /path/to/agent-memory diff --stat
git -C /path/to/agent-memory add .
git -C /path/to/agent-memory commit -m "Update continuity memory"
```

Do not auto-commit by default. The operator should see what memory is becoming
durable before it leaves the machine.

## First Machine Setup

1. Create a **private** empty Git repo for memory.

   Example names:

   - `agent-continuity-memory`
   - `my-agent-memory`
   - `company-agent-memory`

   Keep this separate from the public `agent-continuity-layer` repo. The layer
   is tooling; the memory repo is yours.

2. Clone the private memory repo.

   ```bash
   git clone https://github.com/YOU/agent-continuity-memory.git "$HOME/agent-continuity-memory"
   ```

3. Initialize the memory layout.

   ```bash
   agent-continuity git-memory --path "$HOME/agent-continuity-memory" init
   ```

4. Export curated continuity state.

   ```bash
   agent-continuity git-memory --path "$HOME/agent-continuity-memory" export
   ```

5. Review, commit, and push.

   ```bash
   git -C "$HOME/agent-continuity-memory" status --short
   git -C "$HOME/agent-continuity-memory" diff --stat
   git -C "$HOME/agent-continuity-memory" add .
   git -C "$HOME/agent-continuity-memory" commit -m "Initial continuity memory export"
   git -C "$HOME/agent-continuity-memory" push -u origin main
   ```

6. Optionally set a default path for interactive shells.

   ```bash
   export AGENT_CONTINUITY_MEMORY_REPO="$HOME/agent-continuity-memory"
   ```

   Put that line in your shell profile if you want `git-memory` commands to
   find the repo without `--path`. Cron jobs should still use absolute paths.

## Second Machine Setup

### One-shot (v0.4.2+)

On another Mac, PC, or VM, the bootstrap one-liner accepts `--memory-repo` (and an optional `--memory-path`). One command does install + signature verification + wiring + memory clone + initial sync:

```bash
curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh \
  | bash -s -- \
      --connect-all --watch --upgrade \
      --memory-repo git@github.com:YOU/agent-continuity-memory.git
```

What happens, in order:
1. Resolves latest release tag, downloads tarball + sha256 + sig + crt
2. Verifies sha256 and cosign signature
3. Runs install.sh into `$XDG_DATA_HOME/agent-continuity/v{X.Y.Z}/`
4. Runs `connect all --apply` (because `--connect-all`)
5. Enables the agent-home watcher LaunchAgent (because `--watch`)
6. Clones `--memory-repo` to `$HOME/agent-continuity-memory` (or `--memory-path` if you set one)
7. Runs `git-memory sync` to populate the local substrate state from the memory repo

The clone step is idempotent. If the target path already has a `.git/` directory, the clone is skipped and only `sync` runs — so re-running the one-liner on the same machine just refreshes the binary and re-syncs.

Failure-tolerant: a failed clone or sync (auth issue, network, conflict) logs a warning and leaves you with a complete install + wiring. The substrate is fully usable; you can retry the memory step with `agent-continuity git-memory --path "$HOME/agent-continuity-memory" sync`.

### Step-by-step (if you want to see each step before any code runs)

1. Install the continuity layer.

   ```bash
   curl -fsSL https://github.com/KING-MOM/agent-continuity-layer/releases/latest/download/bootstrap.sh | bash -s -- --connect-all
   ```

2. Clone the same private memory repo.

   ```bash
   git clone https://github.com/YOU/agent-continuity-memory.git "$HOME/agent-continuity-memory"
   ```

3. Pull current memory and verify status.

   ```bash
   agent-continuity git-memory --path "$HOME/agent-continuity-memory" sync
   agent-continuity git-memory --path "$HOME/agent-continuity-memory" status
   ```

Either path leaves the second machine with the durable continuity memory: decisions, context snapshots, curated project summaries, handoffs, and artifact indexes. It does not receive raw local sessions, credentials, cookies, machine-local trust grants, or signing material.

## Asking An Agent To Set It Up

If a shell-capable agent is pointed at this repo, the instruction can be:

> Install `agent-continuity-layer`, create or clone my private memory repo,
> initialize Git-backed memory, run `git-memory sync`, and set up the same flow
> on this machine. Do not put private memory in the public layer repo.

The agent should read this document, install/connect the layer, clone the
operator's private memory repo, and use `agent-continuity git-memory --path
... sync`.

## Automatic Sync

For daily Mac-to-Mac continuity, prefer a macOS LaunchAgent that runs `sync`,
not a symlink and not cron. Cron does not run while a laptop is asleep; launchd
will run at the next reasonable wake opportunity.

Create `~/Library/LaunchAgents/com.agent-continuity.git-memory-sync.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.agent-continuity.git-memory-sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/.local/bin/agent-continuity</string>
    <string>git-memory</string>
    <string>--path</string>
    <string>/Users/YOU/agent-continuity-memory</string>
    <string>sync</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>0</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/YOU/.local/state/agent-continuity/git-memory-sync.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/YOU/.local/state/agent-continuity/git-memory-sync.err.log</string>
</dict>
</plist>
```

Then load it:

```bash
launchctl load "$HOME/Library/LaunchAgents/com.agent-continuity.git-memory-sync.plist"
```

`sync` uses GitHub as the source of truth:

1. Refuses to start if the memory repo has uncommitted local changes.
2. Pulls `origin/main` with `--rebase`.
3. Exports curated local continuity state.
4. Runs a high-confidence secret scan.
5. Commits and pushes only when actual memory files changed.
6. Adds device, substrate version, export time, and remote to commit metadata.
7. Retries one push race by rebasing once more.

This gives cross-device freshness without making live runtime folders part of
Git. A Mac can always recover the latest durable memory by pulling the private
memory repo before starting work.
