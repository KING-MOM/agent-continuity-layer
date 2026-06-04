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

## Compiling a session into structured decisions (M17.1)

`transcript compile` reads a session's JSONL, extracts tool-call events into structured decision-log entries, and appends them to the canonical `decisions.jsonl`. Idempotent: re-compiling the same session is a no-op.

```bash
# Preview what would be compiled (dry-run, no writes)
agent-continuity transcript compile 15083edc

# Write new entries to decisions.jsonl
agent-continuity transcript compile 15083edc --apply

# Compile + JSON output (for scripting / inspecting candidates)
agent-continuity transcript compile 15083edc --json
```

### Privacy invariants (load-bearing)

**Compiled entries NEVER include:**
- Raw user / assistant message text content
- `tool_use.input.new_string` / `old_string` / `content` / `prompt`
- Bash command stdout / stderr
- File contents
- Anything outside structured tool call metadata

**The compile output is structured-only, machine-derived from tool call shape.** That makes the decisions log safe to sync across devices without leaking conversation content.

### Privacy denylist (event-level skip)

Events get dropped entirely (not redacted partially) when they match:

| Pattern | Source |
|---|---|
| File paths under `**/credentials/**`, `**/.env*`, `**/secrets/**`, `**/id_rsa*`, `**/.ssh/**`, `**/.gpg/**`, `**/keychain*`, `**/.aws/credentials`, `**/.netrc` | `Edit.file_path` / `Write.file_path` |
| Bash commands containing `sk-…`, `ghp_…`, `AKIA…`, `xoxb-…`, `AIza…` patterns | `Bash.command` |
| Bash commands containing `BEGIN PRIVATE KEY` markers | `Bash.command` |
| File edits to paths OUTSIDE the session's `cwd` | `Edit.file_path` / `Write.file_path` |
| File edits to non-load-bearing paths inside cwd (anything not under `docs/`, `scripts/`, `core/`, `bin/`, `.github/`, or top-level `README/CHARTER/CONTRIBUTING/SECURITY/LICENSE`) | `Edit.file_path` / `Write.file_path` |

A debug override exists (`--no-privacy-filter`) but emits a loud `WARNING` when combined with `--apply`.

### Extraction heuristics

| Tool call shape | Becomes decision |
|---|---|
| Bash `git commit -m "<subject>"` | `committed: <subject>` |
| Bash `gh release create v<X.Y.Z> …` | `released: v<X.Y.Z>` |
| Bash `git tag [-a] v<X.Y.Z>` | `tagged: v<X.Y.Z>` |
| Bash `brew/pip/npm install <pkg>` | `installed: <pkg> (via <mgr>)` |
| Edit / Write to `docs/`, `scripts/`, `core/`, `bin/`, `.github/`, or top-level READMEs | `edited: <rel-path>` / `wrote: <rel-path>` |
| AskUserQuestion with header `<H>` | `asked operator: <H>` (one per question) |
| Bash `agent-continuity decisions add --decision X --why Y --ref Z` | operator-explicit pass-through (the operator's text was already destined for the log) |

### Compiled entry shape

Every compiled entry lands in `decisions.jsonl` with:

```json
{
  "id": "sha256:…",
  "ts": "2026-05-28T10:30:00Z",
  "adapter": "claude",
  "author": "auto:transcript-compile@15083edc",
  "repo": "agent-continuity-layer",
  "decision": "<≤120 chars, structured>",
  "why": "<≤200 chars, structured>",
  "refs": ["session:15083edc-…", "tool:Bash", "commit:…"]
}
```

The `author: "auto:transcript-compile@<prefix>"` lets you filter compiled-from-transcript vs manually-appended:

```bash
agent-continuity decisions list --author "auto:transcript-compile*"
```

### Idempotency guarantee

Each compiled entry's `id` is `sha256(canonical body)`. Since `ts` is sourced from the (deterministic) tool call timestamp — not from compile-time `now()` — the same event in the same session produces the same id every time.

Before appending, the compile reads the existing `decisions.jsonl`, builds a set of present ids, and skips any candidate whose id is already in the log. Re-compiling a session that's already been compiled writes 0 entries.

### What's not in M17.1

## What's coming in M17.2

- **M17.2 — LLM-based summary** (`agent-continuity transcript summarize <id>`): for sessions where the heuristic compile is insufficient (purely conversational decisions, strategic discussions without tool follow-through), run a configurable LLM over the transcript and append synthesized decision entries. Operator opt-in per session because real money cost. Same privacy invariants as M17.1: never includes raw chat content; only structured JSON summary entries.

The decisions log is the merge point. Whether a decision was appended by hand, by the heuristic compile, or by an LLM summary, it lives in the same JSONL and syncs via existing M10 infrastructure. The `author` field distinguishes the provenance.

## Privacy considerations

The transcripts under `~/.claude/projects/` often contain pasted secrets, credentials, and private code. `agent-continuity transcript` reads them but does not export or sync them anywhere. The information it surfaces (title, cwd, tool call counts, durations) is metadata only, not content.

M17.1's compile path will add explicit denylist patterns to keep secret-like strings out of the synced decisions log. Until then, the transcripts you read with `transcript show` are still purely local.

## See also

- [`docs/handoff.md`](handoff.md) — device-to-device state handoff; does NOT auto-restore transcripts cross-username (path-encoding mismatch)
- [`docs/quickstart.md`](quickstart.md) — first delegated task
- [`docs/m9-adapter-pattern.md`](m9-adapter-pattern.md) — the six-operation adapter contract that this layer plugs into
