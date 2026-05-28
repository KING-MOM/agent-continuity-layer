# Device-to-device handoff

`agent-continuity handoff` packages this device's substrate state — and optionally Claude Code session transcripts — into a single tar.gz that another device can ingest. Use case: switching between your desktop and your laptop without re-establishing context manually.

## Quick path

**On the source device:**

```bash
# default: agent-continuity state only (decisions log, registry, trust policy, queue)
agent-continuity handoff export --to /tmp/handoff.tar.gz

# include Claude Code session transcripts (opt-in)
agent-continuity handoff export --to /tmp/handoff.tar.gz --include-claude
```

**Transfer the file** (scp, rsync, AirDrop, USB drive — any byte-exact channel).

**On the target device:**

```bash
# preview without extracting
agent-continuity handoff inspect /tmp/handoff.tar.gz

# import (backs up existing state first)
agent-continuity handoff import /tmp/handoff.tar.gz
```

## What gets exported

By default:

- `~/.config/agent-continuity/` — trust policy, project registry
- `~/.local/state/agent-continuity/` — decisions log
- `~/.cache/agent-continuity/queue/` — worker task queue (in-flight + done)

With `--include-claude`:

- `~/.claude/projects/` — Claude Code session transcripts (`.jsonl` files)

Notes:

- Transcripts can be hundreds of MB across many sessions. Inspect the bundle size before transferring over slow links.
- Transcripts often contain pasted code, secrets, and prompts. Treat the bundle accordingly.

With `--no-state`:

- Skips agent-continuity state entirely. Combine with `--include-claude` for "just my Claude sessions, no substrate state".

## What gets imported

Default import:

1. **Backs up** any existing target state to `~/.local/share/agent-continuity-handoff-backup-<TIMESTAMP>/`. Restore by moving subdirs back to their XDG roots if needed.
2. **Writes** the bundle's contents into the target's XDG roots.
3. **Skips Claude restoration when source HOME ≠ target HOME** (see path-encoding gotcha below).
4. Prints a summary of how many files were restored per category.

With `--no-backup`:

- Skips the backup step. Destructive — existing state is overwritten in place. Use only when you know what you're doing.

## Path-encoding gotcha for Claude sessions

Claude Code stores sessions under `~/.claude/projects/<encoded-absolute-path>/`. The encoded directory name is derived from the absolute path the user was working from when the session was created, e.g. `-Users-operator` (`/Users/<operator>`).

If you transfer a Claude-included bundle to a device with a different HOME (different username, e.g. `/Users/<operator>ricio` instead of `/Users/<operator>`), Claude Code on the target won't find the transferred sessions because the encoded dirname won't match.

M16.0 handles this by:

1. Recording source HOME in the manifest.
2. On import, comparing source HOME against target HOME.
3. If they differ AND the bundle includes Claude sessions: **skipping** Claude restoration and printing a clear warning. The transcripts stay inside the tarball; extract manually if you want to massage path names yourself.

This is intentionally conservative for M16.0. A future slice may add path-rewrite support (rewriting both the encoded directory name and any embedded path references in the JSONL), but that requires more care than M16.0 is willing to commit to.

For the common case (same username on both machines), restoration is automatic.

## Manifest format

Every bundle has a `handoff/manifest.json`:

```json
{
  "schema_version": "1.0",
  "created_at": "2026-05-28T01:23:45Z",
  "source": {
    "device_hostname": "macmini",
    "home": "/Users/<operator>",
    "substrate_version": "0.1.8"
  },
  "included": {
    "agent_continuity_config": true,
    "agent_continuity_state": true,
    "agent_continuity_queue": true,
    "claude_sessions": false
  },
  "file_counts": {
    "config": 3,
    "state": 1,
    "cache-queue": 7,
    "claude": 0
  }
}
```

`inspect` extracts and prints this manifest without touching anything else.

## Common patterns

**Move everything substrate-related plus your current Claude session, same username on both machines:**

```bash
# source
agent-continuity handoff export --to /tmp/handoff.tar.gz --include-claude
scp /tmp/handoff.tar.gz user@laptop:/tmp/

# target
ssh user@laptop
agent-continuity handoff inspect /tmp/handoff.tar.gz
agent-continuity handoff import /tmp/handoff.tar.gz
```

**Migrate just the decision log and project registry (e.g., to a fresh install):**

```bash
agent-continuity handoff export --to /tmp/handoff.tar.gz
# transfer
agent-continuity handoff import /tmp/handoff.tar.gz
```

**Inspect a bundle someone else sent you, without committing to import:**

```bash
agent-continuity handoff inspect /tmp/handoff.tar.gz
# review manifest, decide whether to import
```

## Out of scope for M16.0

- **Cross-username Claude session restoration.** Today: skipped with warning. Future: optional rewrite mode.
- **Selective restore.** Today: import is all-or-nothing for what the manifest contains. Future: `--only-decisions`, `--only-registry`, etc.
- **Diff preview before import.** Today: `inspect` shows the manifest. Future: show per-file diffs against existing target state.
- **Compression tuning.** Today: standard gzip. Bundles with many small Claude transcripts could benefit from `.tar.zst`; deferred.
- **Encryption.** Today: bundle is plain tar.gz. If sending over an untrusted channel, encrypt at the transport layer (scp, signed envelope, etc.). The substrate has no opinion on transport.

## Relationship to M9.1 bundle and M10 sync

`handoff` is a third axis:

| Mechanism | Purpose | When to use |
|---|---|---|
| **M9.1 `bundle export/ingest`** | operator-mediated handoff between adapters | give continuity state to a web agent or other adapter that can't shell out |
| **M10 `sync push/pull`** | bidirectional state sync via a VM | continuous shared state between devices via a shared backend |
| **M16.0 `handoff export/import`** | one-shot device-to-device migration | "I'm switching laptops" or "I want my desktop state on my dev box" |

They don't conflict and the substrate doesn't try to unify them. Each solves a different shape of "memory needs to move."

## See also

- [`docs/install.md`](install.md) — bootstrap install on the target device
- [`docs/trust-policy.md`](trust-policy.md) — trust model the imported state participates in
- [`docs/m9-adapter-pattern.md`](m9-adapter-pattern.md) — the bundle path for adapter-mediated handoff
