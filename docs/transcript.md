# Local Claude Code transcript index

`agent-continuity transcript` exposes a read-only inventory of Claude Code session transcripts living locally under `~/.claude/projects/`. M17.0 ships the index; later slices (M17.1, M17.2) build on top of it to compile transcripts into structured decisions.

## Quick path

```bash
# list all local sessions, most recent first
agent-continuity transcript list

# narrow to sessions whose cwd contains 'agent-continuity-layer'
agent-continuity transcript list --repo agent-continuity-layer

# show one session in detail (full id or unique prefix)
agent-continuity transcript show 15083edc

# emit the absolute path to a session's JSONL (useful for piping)
agent-continuity transcript path 15083edc
```

## What gets extracted per session

For each `.jsonl` file under `~/.claude/projects/<encoded>/<uuid>.jsonl`:

- **session_id** — the UUID part of the filename
- **ai_title** — Claude's auto-generated short title for the session, if present
- **cwd** — the working directory the session ran in (modal across messages)
- **git_branch** — the git branch context (modal across messages)
- **claude_code_version** — version of Claude Code that produced the transcript
- **started_at / last_at / duration_seconds** — wall-clock span
- **message_count** — split by user / assistant role
- **tool_call_counts** — per-tool breakdown (Bash, Edit, Write, Read, etc.)
- **size_bytes** — raw JSONL file size
- **encoded_project_path / decoded_project_path** — the Claude Code encoded directory name and a best-effort human reconstruction
- **jsonl_path** — absolute path to the file

Example show output:

```
session:   15083edc-e4e5-4e19-8308-7536210ea8cf
  title:           Design life-agents-unified infrastructure
  cwd:             /Users/mau/.openclaw/workspace/agent-continuity-layer
  git branch:      main
  claude code:     v2.1.145
  started:         2026-05-23T19:29:00.490Z
  last:            2026-06-04T16:40:04.526Z (285h 11m)
  messages:        4365 (user: 1658, assistant: 2707)
  tool calls:      1513 (Bash:724, Edit:324, TodoWrite:186, Write:162, …)
  size:            17.9 MB
```

That's the "remember this session?" answer in 10 lines: title, what repo, when, how long, what tool surface was exercised.

## What it does NOT do (M17.0 scope)

- Does not modify the transcripts
- Does not write substrate decisions (M17.1 will)
- Does not synthesize a textual summary (M17.2 will)
- Does not sync transcripts across devices
- Does not parse message content beyond counting tool calls

## Use cases right now

1. **"Which sessions ran in this repo?"**
   ```bash
   agent-continuity transcript list --repo agent-continuity-layer
   ```
2. **"What was that session yesterday about?"**
   ```bash
   agent-continuity transcript list --limit 5
   # → see titles + cwd, pick one, then:
   agent-continuity transcript show <prefix>
   ```
3. **"Pipe a session's content into a tool"**
   ```bash
   agent-continuity transcript path 15083edc | xargs grep "decision"
   ```
4. **Feed the index to an agent via MCP** — once M17 wires the index as MCP tools (deferred), an agent can call `list_local_sessions` / `show_session` to discover what work happened locally.

## What's coming in M17.1 and M17.2

- **M17.1 — Heuristic compile** (`agent-continuity transcript compile <id>`): scan the JSONL for load-bearing events (git commits, tool call patterns, explicit "decided to X" patterns) and append structured entries to the canonical `decisions.jsonl`. Privacy: allowlist/denylist of patterns; explicit `--apply` required.
- **M17.2 — LLM-based summary** (`agent-continuity transcript summarize <id>`): for sessions where the heuristic compile is insufficient, run a configurable LLM over the transcript and append synthesized decision entries. Operator opt-in per session because real money cost.

The decisions log is the merge point. Whether a decision was appended by hand, by the heuristic compile, or by an LLM summary, it lives in the same JSONL and syncs via existing M10 infrastructure. The `author` field distinguishes the provenance.

## Privacy considerations

The transcripts under `~/.claude/projects/` often contain pasted secrets, credentials, and private code. `agent-continuity transcript` reads them but does not export or sync them anywhere. The information it surfaces (title, cwd, tool call counts, durations) is metadata only, not content.

M17.1's compile path will add explicit denylist patterns to keep secret-like strings out of the synced decisions log. Until then, the transcripts you read with `transcript show` are still purely local.

## See also

- [`docs/handoff.md`](handoff.md) — device-to-device state handoff; does NOT auto-restore transcripts cross-username (path-encoding mismatch)
- [`docs/quickstart.md`](quickstart.md) — first delegated task
- [`docs/m9-adapter-pattern.md`](m9-adapter-pattern.md) — the six-operation adapter contract that this layer plugs into
