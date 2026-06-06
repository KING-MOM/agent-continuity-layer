#!/usr/bin/env python3
"""_transcript.py — M17.0 local Claude Code transcript index.

Continuity primitive: handoff ledger + project registry (extends both
with awareness of Claude Code session transcripts that exist locally).

Reads Claude Code session transcripts from `~/.claude/projects/<encoded>/
<session-uuid>.jsonl` and exposes them via a structured CLI. Pure
indexing — does NOT modify the transcripts, does NOT write substrate
decisions, does NOT sync anything across devices.

Subcommands:
  list                                  list all local sessions with metadata
  show <session-id-or-prefix>           show one session's metadata in detail
  path <session-id-or-prefix>           emit the absolute path to the JSONL

The JSONL format Claude Code uses (as of 2026-06) is one JSON object per
line, with `type` field tagging the entry. The types we extract from:

  - user / assistant: the actual messages. Carry `cwd`, `gitBranch`,
    `version`, `timestamp`, `sessionId`, `message.role`, `message.content`.
  - ai-title: a Claude-generated short title for the whole session.
    Carries `aiTitle`.
  - file-history-snapshot, attachment, queue-operation, last-prompt,
    system: metadata events we currently skip (not part of the index).

Per-session output fields:
  - session_id, encoded_project_path, jsonl_path, size_bytes
  - ai_title (the Claude-generated session title, if present)
  - cwd (modal cwd across messages — the "working directory" of the session)
  - git_branch (modal gitBranch, when present)
  - claude_code_version (modal version)
  - started_at / last_at / duration_seconds
  - message_count (split by user / assistant)
  - tool_call_counts (per tool name, scanning assistant content blocks)
  - tools_used (set of distinct tool names)

This is M17.0 — read-only inventory. M17.1 (shipped) adds compile-to-decisions
via heuristic extraction. M17.2 (LLM-based summarization) was considered and
dropped — see docs/transcript.md bottom for the reasoning.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from collections import Counter
from typing import Any, Iterable

CLAUDE_PROJECTS_DIR = pathlib.Path.home() / ".claude" / "projects"


# ────────────────────────────────────────────────────────────────
# JSONL parsing

def _iter_jsonl(path: pathlib.Path) -> Iterable[dict]:
    """Yield each parseable JSON object from a JSONL file. Silently
    skips malformed lines — transcripts can have partial writes at
    the end if Claude Code crashed mid-session."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _decode_encoded_path(encoded: str) -> str:
    """Best-effort reversal of Claude Code's path encoding.
    Heuristic: `--` in the encoded path appears to map to `/.` in the
    original (the `.` of dot-prefixed dirs like `.openclaw`). Single
    `-` maps to `/`. This is lossy for paths that legitimately contain
    `--` but rare enough in practice that we accept the imprecision."""
    # Split on `--` first to find the "/.X" boundaries
    parts = encoded.split("--")
    # First part: `-Users-mau` → `/Users/mau`
    decoded_parts = [parts[0].replace("-", "/")]
    # Subsequent parts: each was preceded by `/.` originally
    for p in parts[1:]:
        decoded_parts.append("/." + p.replace("-", "/"))
    return "".join(decoded_parts)


def _scan_session(jsonl_path: pathlib.Path) -> dict[str, Any] | None:
    """Read a session JSONL and return a metadata dict. Returns None if
    the file is unreadable or contains no recognizable session content."""
    session_id = jsonl_path.stem
    if not jsonl_path.is_file():
        return None

    ai_title: str | None = None
    cwd_counter: Counter[str] = Counter()
    branch_counter: Counter[str] = Counter()
    version_counter: Counter[str] = Counter()
    tool_calls: Counter[str] = Counter()
    msg_user = 0
    msg_assistant = 0
    timestamps: list[str] = []

    for entry in _iter_jsonl(jsonl_path):
        t = entry.get("type")
        if t == "ai-title":
            v = entry.get("aiTitle")
            if isinstance(v, str) and v.strip():
                ai_title = v.strip()
            continue
        if t not in ("user", "assistant"):
            continue

        # Track per-message metadata
        ts = entry.get("timestamp")
        if isinstance(ts, str):
            timestamps.append(ts)
        cwd = entry.get("cwd")
        if isinstance(cwd, str):
            cwd_counter[cwd] += 1
        branch = entry.get("gitBranch")
        if isinstance(branch, str) and branch:
            branch_counter[branch] += 1
        version = entry.get("version")
        if isinstance(version, str):
            version_counter[version] += 1

        if t == "user":
            msg_user += 1
        else:
            msg_assistant += 1
            # Scan assistant message content for tool_use blocks
            msg = entry.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name")
                        if isinstance(name, str):
                            tool_calls[name] += 1

    if not timestamps:
        # Empty / metadata-only transcript — skip
        return None

    timestamps.sort()
    started_at = timestamps[0]
    last_at = timestamps[-1]
    duration_seconds = _duration_seconds(started_at, last_at)

    # The encoded project path is the parent directory name of the jsonl file
    encoded_project_path = jsonl_path.parent.name
    decoded_project_path = _decode_encoded_path(encoded_project_path)

    return {
        "session_id": session_id,
        "jsonl_path": str(jsonl_path),
        "encoded_project_path": encoded_project_path,
        "decoded_project_path": decoded_project_path,
        "ai_title": ai_title,
        "cwd": cwd_counter.most_common(1)[0][0] if cwd_counter else None,
        "git_branch": branch_counter.most_common(1)[0][0] if branch_counter else None,
        "claude_code_version": version_counter.most_common(1)[0][0] if version_counter else None,
        "started_at": started_at,
        "last_at": last_at,
        "duration_seconds": duration_seconds,
        "message_count": msg_user + msg_assistant,
        "message_count_user": msg_user,
        "message_count_assistant": msg_assistant,
        "tool_call_counts": dict(tool_calls.most_common()),
        "tools_used": sorted(tool_calls.keys()),
        "size_bytes": jsonl_path.stat().st_size,
    }


def _duration_seconds(start_iso: str, end_iso: str) -> int | None:
    try:
        start = dt.datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return int((end - start).total_seconds())
    except (ValueError, TypeError):
        return None


def _format_duration(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "?"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


# ────────────────────────────────────────────────────────────────
# Discovery

def _find_all_sessions() -> list[pathlib.Path]:
    """Walk ~/.claude/projects/ and return every .jsonl file."""
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return []
    return sorted(CLAUDE_PROJECTS_DIR.glob("*/*.jsonl"))


def _find_by_prefix(prefix: str) -> list[pathlib.Path]:
    """Return jsonl paths whose session_id begins with `prefix`."""
    matches = []
    for p in _find_all_sessions():
        if p.stem.startswith(prefix):
            matches.append(p)
    return matches


# ────────────────────────────────────────────────────────────────
# CLI subcommands

def cmd_list(args: argparse.Namespace) -> int:
    sessions: list[dict[str, Any]] = []
    for jsonl in _find_all_sessions():
        meta = _scan_session(jsonl)
        if meta is None:
            continue
        if args.repo:
            cwd = meta.get("cwd") or meta.get("decoded_project_path") or ""
            if args.repo not in cwd:
                continue
        sessions.append(meta)

    # Sort by start time descending (most recent first)
    sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    if args.limit and args.limit > 0:
        sessions = sessions[: args.limit]

    if args.json:
        print(json.dumps({"sessions": sessions}, indent=2, ensure_ascii=False))
        return 0

    if not sessions:
        print("(no Claude Code sessions found under ~/.claude/projects/)")
        return 0

    # Human table
    print(f"{'session':12}  {'started':16}  {'dur':>8}  {'msgs':>5}  {'tools':>5}  {'title / cwd'}")
    print("-" * 100)
    for s in sessions:
        sid = s["session_id"][:10] + "…"
        start = (s.get("started_at") or "")[:16].replace("T", " ")
        dur = _format_duration(s.get("duration_seconds"))
        msgs = s["message_count"]
        tools = sum(s["tool_call_counts"].values())
        title = s.get("ai_title") or "(untitled)"
        cwd = s.get("cwd") or s.get("decoded_project_path") or "?"
        # Truncate title + cwd to fit
        descr = f"{title} · {cwd}"
        if len(descr) > 60:
            descr = descr[:57] + "…"
        print(f"{sid:12}  {start:16}  {dur:>8}  {msgs:>5}  {tools:>5}  {descr}")
    print()
    print(f"{len(sessions)} session(s)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    matches = _find_by_prefix(args.session_id)
    if not matches:
        print(f"error: no session id starts with {args.session_id!r}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: prefix {args.session_id!r} matched {len(matches)} sessions:", file=sys.stderr)
        for m in matches[:5]:
            print(f"  {m.stem}", file=sys.stderr)
        return 1
    meta = _scan_session(matches[0])
    if meta is None:
        print(f"error: session {matches[0].name} has no parseable content", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(meta, indent=2, ensure_ascii=False))
        return 0

    print(f"session:   {meta['session_id']}")
    if meta.get("ai_title"):
        print(f"  title:           {meta['ai_title']}")
    print(f"  cwd:             {meta.get('cwd') or '(unknown)'}")
    if meta.get("git_branch"):
        print(f"  git branch:      {meta['git_branch']}")
    if meta.get("claude_code_version"):
        print(f"  claude code:     v{meta['claude_code_version']}")
    print(f"  encoded path:    {meta['encoded_project_path']}")
    if meta.get("decoded_project_path") != meta.get("cwd"):
        print(f"  decoded path:    {meta['decoded_project_path']}  (best-effort)")
    print(f"  started:         {meta['started_at']}")
    print(f"  last:            {meta['last_at']} ({_format_duration(meta.get('duration_seconds'))})")
    print(f"  messages:        {meta['message_count']} (user: {meta['message_count_user']}, "
          f"assistant: {meta['message_count_assistant']})")
    if meta["tool_call_counts"]:
        total = sum(meta["tool_call_counts"].values())
        per_tool = ", ".join(f"{n}:{c}" for n, c in meta["tool_call_counts"].items())
        print(f"  tool calls:      {total} ({per_tool})")
    else:
        print(f"  tool calls:      0")
    print(f"  size:            {_format_bytes(meta['size_bytes'])}")
    print(f"  jsonl:           {meta['jsonl_path']}")
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    """M17.1: heuristic compile of one session's tool calls into structured
    decision-log entries. Read-only by default (--dry-run); --apply writes
    new entries to the canonical decisions.jsonl. Idempotent: re-compiling
    the same session is a no-op."""
    # Import lazily so `list/show/path` don't pay the import cost.
    from _transcript_compile import compile_session

    matches = _find_by_prefix(args.session_id)
    if not matches:
        print(f"error: no session id starts with {args.session_id!r}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: prefix {args.session_id!r} matched {len(matches)} sessions:", file=sys.stderr)
        for m in matches[:5]:
            print(f"  {m.stem}", file=sys.stderr)
        return 1

    if args.no_privacy_filter and args.apply:
        print(
            "WARNING: --no-privacy-filter is set with --apply. Secret patterns "
            "and sensitive file paths will NOT be filtered. Use only for debugging.",
            file=sys.stderr,
        )

    result = compile_session(
        matches[0],
        apply=args.apply,
        no_privacy_filter=args.no_privacy_filter,
    )

    if args.json:
        print(json.dumps({
            "session_id": result.session_id,
            "candidates": [
                {
                    "ts": c.ts,
                    "decision": c.decision,
                    "why": c.why,
                    "refs": c.refs,
                    "source_type": c.source_type,
                    "source_tool": c.source_tool,
                }
                for c in result.candidates
            ],
            "skipped_sensitive": result.skipped_sensitive,
            "skipped_existing": result.skipped_existing,
            "written": result.written,
            "applied": args.apply,
        }, indent=2, ensure_ascii=False))
        return 0

    print(f"session:           {result.session_id}")
    print(f"  candidates:      {len(result.candidates)}")
    print(f"  skipped (privacy): {result.skipped_sensitive}")
    print(f"  skipped (already in log): {result.skipped_existing}")
    if args.apply:
        print(f"  written:         {len(result.written)}")
    else:
        new_count = len(result.candidates) - result.skipped_existing
        print(f"  would write:     {new_count} (dry-run; pass --apply to commit)")

    if result.candidates and not args.json:
        print()
        print("candidates:")
        for c in result.candidates:
            tag = c.source_type
            print(f"  [{tag:24}] {c.ts}  {c.decision}")
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    matches = _find_by_prefix(args.session_id)
    if not matches:
        print(f"error: no session id starts with {args.session_id!r}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: prefix {args.session_id!r} matched {len(matches)} sessions:", file=sys.stderr)
        for m in matches[:5]:
            print(f"  {m.stem}", file=sys.stderr)
        return 1
    print(matches[0])
    return 0


# ────────────────────────────────────────────────────────────────
# Entry point

def main() -> int:
    ap = argparse.ArgumentParser(
        prog="transcript",
        description=(
            "M17.0 local Claude Code transcript index. Read-only inventory "
            "of session JSONL files under ~/.claude/projects/. Does not "
            "modify transcripts and does not sync them across devices."
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list all local sessions with metadata")
    p_list.add_argument("--json", action="store_true", help="emit JSON")
    p_list.add_argument("--limit", type=int, default=0, help="cap to N most recent")
    p_list.add_argument(
        "--repo",
        help="filter to sessions whose cwd contains this substring",
    )
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one session's metadata in detail")
    p_show.add_argument("session_id", help="session id or unique prefix")
    p_show.add_argument("--json", action="store_true", help="emit JSON")
    p_show.set_defaults(func=cmd_show)

    p_path = sub.add_parser("path", help="emit the absolute path to a session's JSONL")
    p_path.add_argument("session_id", help="session id or unique prefix")
    p_path.set_defaults(func=cmd_path)

    p_compile = sub.add_parser(
        "compile",
        help="compile a session's tool calls into structured decision-log entries (M17.1)",
    )
    p_compile.add_argument("session_id", help="session id or unique prefix")
    p_compile.add_argument(
        "--apply",
        action="store_true",
        help="write new entries to decisions.jsonl (default: dry-run, no writes)",
    )
    p_compile.add_argument(
        "--no-privacy-filter",
        action="store_true",
        help=(
            "DEBUG ONLY: disable the secret-pattern denylist and file-path "
            "denylist. Logs a WARNING when combined with --apply."
        ),
    )
    p_compile.add_argument("--json", action="store_true", help="emit JSON")
    p_compile.set_defaults(func=cmd_compile)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
