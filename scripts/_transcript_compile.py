#!/usr/bin/env python3
"""_transcript_compile.py — M17.1 heuristic compile of Claude Code session
transcripts into structured decision-log entries.

Strict privacy invariant: NEVER includes raw chat message text, NEVER
includes contents of tool_use.input free-form fields (new_string, content,
prompt, etc.), NEVER includes stdout/stderr from command results. Every
decision entry produced is derived solely from structured tool call
metadata + git history (which is already public via the repo).

What gets compiled (in order of confidence):
  1. agent-continuity decisions add invocations (operator-explicit; pass-through)
  2. Git commits made during the session (Bash `git commit -m ...`)
  3. GitHub release / tag creation (Bash `gh release create`, `git tag`)
  4. Package installs (Bash `brew/pip/npm install ...`)
  5. File edits within the session's cwd (Edit, Write to load-bearing dirs)
  6. AskUserQuestion answers (structured question_header + selected label)

What does NOT get compiled:
  - User / assistant message text content
  - tool_use.input.new_string / old_string / content / prompt
  - Bash command stdout/stderr
  - Files edited outside the session's cwd (could leak unrelated paths)
  - Anything matching privacy denylist patterns

Privacy denylist (event-level skip):
  - File paths under credentials/, .env*, secrets/, id_rsa*, .ssh/, .gpg/, keychain*
  - Bash commands containing API key patterns (sk-, ghp_, AKIA*, xoxb-, etc.)
  - Bash commands containing private key markers (BEGIN PRIVATE KEY)

Idempotency: pre-computes each entry's sha256 id, reads the existing
decisions log to build a set of present ids, and only appends entries
whose id is new. Re-compiling a session is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shlex
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Reuse decisions internals.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _decisions import (  # noqa: E402
    append_entries_from_transcript_compile,
    _iter_entries,
    DECISIONS_PATH,
    SCHEMA_VERSION,
    _compute_id,
    _merge_auto_ref,
)


# ────────────────────────────────────────────────────────────────
# Privacy denylist

_FILE_PATH_DENYLIST = [
    re.compile(r"/credentials/"),
    re.compile(r"/\.env(\.|/|$)"),
    re.compile(r"/secrets/"),
    re.compile(r"/id_rsa(\.|$)"),
    re.compile(r"/\.ssh/"),
    re.compile(r"/\.gpg/"),
    re.compile(r"/keychain", re.IGNORECASE),
    re.compile(r"/\.aws/credentials"),
    re.compile(r"/\.netrc(\.|$)"),
]

_BASH_SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),       # OpenAI / Anthropic style
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),        # GitHub personal access tokens
    re.compile(r"\bAKIA[0-9A-Z]{12,}"),           # AWS access keys
    re.compile(r"\bxox[bpoa]-[A-Za-z0-9-]{20,}"), # Slack tokens
    re.compile(r"BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),      # Google API keys
]


def _path_is_sensitive(path: str) -> bool:
    if not isinstance(path, str):
        return False
    for pat in _FILE_PATH_DENYLIST:
        if pat.search(path):
            return True
    return False


def _bash_command_is_sensitive(cmd: str) -> bool:
    if not isinstance(cmd, str):
        return False
    for pat in _BASH_SECRET_PATTERNS:
        if pat.search(cmd):
            return True
    return False


# ────────────────────────────────────────────────────────────────
# Structured event extraction (NO chat content)

# Load-bearing directory patterns (relative to cwd). Edit/Write events
# outside these get skipped to avoid noise + reduce path leakage surface.
_LOAD_BEARING_DIR_PATTERNS = [
    re.compile(r"^docs/"),
    re.compile(r"^scripts/"),
    re.compile(r"^core/"),
    re.compile(r"^bin/"),
    re.compile(r"^\.github/"),
    re.compile(r"^README\.md$"),
    re.compile(r"^CHARTER\.md$"),
    re.compile(r"^CONTRIBUTING\.md$"),
    re.compile(r"^SECURITY\.md$"),
    re.compile(r"^LICENSE$"),
]


@dataclass
class CompiledDecision:
    """Internal representation of a compile event before it becomes a
    decisions.jsonl entry. Captures the source info for diagnostic
    output during --dry-run."""

    ts: str
    decision: str
    why: str
    refs: list[str]
    source_type: str   # "git-commit" | "package-install" | "file-edit" | etc.
    source_tool: str   # e.g., "Bash" | "Edit" | "AskUserQuestion"


def _truncate(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"


def _relpath(file_path: str, cwd: str) -> str | None:
    """Return file_path made relative to cwd if it's INSIDE cwd.
    Returns None if file_path is outside cwd (caller skips the event)."""
    if not (file_path and cwd):
        return None
    if not file_path.startswith(cwd):
        return None
    rel = file_path[len(cwd):].lstrip("/")
    if not rel:
        return None
    return rel


def _is_load_bearing(rel_path: str) -> bool:
    for pat in _LOAD_BEARING_DIR_PATTERNS:
        if pat.search(rel_path):
            return True
    return False


# ----- per-event extractors -----

_HEREDOC_COMMIT_RE = re.compile(
    r'git\s+commit\b[^|&;]*?-m\s+"\$\(\s*cat\s+<<-?\s*[\'"]?(?P<delim>\w+)[\'"]?\s*\n'
    r'(?P<body>.*?)'
    r'\n[ \t]*(?P=delim)\b',
    re.DOTALL,
)


def _extract_git_commit(input_obj: dict, ts: str, session_id: str) -> CompiledDecision | None:
    """Heuristic: Bash command containing `git commit -m "..."`.

    Handles two message styles:
      1. Flat:    git commit -m "subject"
      2. Heredoc: git commit -m "$(cat <<'EOF'
                  subject
                  ...body
                  EOF
                  )"
    """
    cmd = input_obj.get("command", "")
    if not isinstance(cmd, str):
        return None
    if "git commit" not in cmd:
        return None

    subject = None

    # Style 2: heredoc-wrapped message. Check first because shlex chokes on these.
    hd = _HEREDOC_COMMIT_RE.search(cmd)
    if hd:
        for line in hd.group("body").split("\n"):
            line = line.strip()
            if line:
                subject = line
                break

    # Style 1: flat -m "..." / '...'. Use shlex.
    if subject is None:
        try:
            tokens = shlex.split(cmd, posix=True)
        except ValueError:
            return None
        if "commit" not in tokens:
            return None
        msg = None
        for i, tok in enumerate(tokens):
            if tok in ("-m", "--message") and i + 1 < len(tokens):
                msg = tokens[i + 1]
                break
        if not msg:
            return None
        subject = msg.strip().split("\n", 1)[0]

    if not subject:
        return None

    return CompiledDecision(
        ts=ts,
        decision=_truncate(f"committed: {subject}", 120),
        why=_truncate("git commit during session", 200),
        refs=[f"tool:Bash"],
        source_type="git-commit",
        source_tool="Bash",
    )


def _extract_release_or_tag(input_obj: dict, ts: str, session_id: str) -> CompiledDecision | None:
    """Heuristic: Bash command creating a release or tag."""
    cmd = input_obj.get("command", "")
    if not isinstance(cmd, str):
        return None
    # gh release create vX.Y.Z
    m = re.search(r"\bgh\s+release\s+create\s+(v[0-9][^\s]*)", cmd)
    if m:
        tag = m.group(1)
        return CompiledDecision(
            ts=ts,
            decision=_truncate(f"released: {tag}", 120),
            why=_truncate("github release created", 200),
            refs=[f"tag:{tag}", "tool:Bash"],
            source_type="github-release",
            source_tool="Bash",
        )
    # git tag -a vX.Y.Z or git tag vX.Y.Z
    m = re.search(r"\bgit\s+tag\s+(?:-a\s+)?(v[0-9][^\s]*)", cmd)
    if m:
        tag = m.group(1)
        return CompiledDecision(
            ts=ts,
            decision=_truncate(f"tagged: {tag}", 120),
            why=_truncate("git tag created", 200),
            refs=[f"tag:{tag}", "tool:Bash"],
            source_type="git-tag",
            source_tool="Bash",
        )
    return None


_PACKAGE_INSTALL_PATTERN = re.compile(
    r"\b(brew|pip|pip3|npm|pnpm|yarn|cargo|gem|apt|apt-get)\s+install\s+([^\s|;&]+)"
)


def _extract_package_install(input_obj: dict, ts: str, session_id: str) -> CompiledDecision | None:
    cmd = input_obj.get("command", "")
    if not isinstance(cmd, str):
        return None
    m = _PACKAGE_INSTALL_PATTERN.search(cmd)
    if not m:
        return None
    pkg_mgr = m.group(1)
    pkg = m.group(2)
    return CompiledDecision(
        ts=ts,
        decision=_truncate(f"installed: {pkg} (via {pkg_mgr})", 120),
        why=_truncate("package install during session", 200),
        refs=[f"package:{pkg}", "tool:Bash"],
        source_type="package-install",
        source_tool="Bash",
    )


def _extract_file_edit(name: str, input_obj: dict, ts: str, cwd: str) -> CompiledDecision | None:
    """Heuristic: Edit/Write tool calls. We extract only the file_path
    (NEVER new_string/content). File must be inside cwd AND match a
    load-bearing directory pattern."""
    if name not in ("Edit", "Write"):
        return None
    file_path = input_obj.get("file_path")
    if not isinstance(file_path, str):
        return None
    rel = _relpath(file_path, cwd)
    if not rel:
        return None  # Outside cwd → skip
    if not _is_load_bearing(rel):
        return None  # Not a load-bearing path → skip
    verb = "wrote" if name == "Write" else "edited"
    return CompiledDecision(
        ts=ts,
        decision=_truncate(f"{verb}: {rel}", 120),
        why=_truncate(f"file modified in session via {name}", 200),
        refs=[f"file:{rel}", f"tool:{name}"],
        source_type=f"file-{name.lower()}",
        source_tool=name,
    )


def _extract_ask_user_question(input_obj: dict, ts: str) -> list[CompiledDecision]:
    """AskUserQuestion: produces one decision per question. The 'answer'
    isn't in the tool_use itself (it's in a subsequent tool_result), so
    M17.1 just records 'asked: <header>' for each question. M17.x can
    extend to pair with answers if needed."""
    results: list[CompiledDecision] = []
    questions = input_obj.get("questions") or []
    if not isinstance(questions, list):
        return results
    for q in questions:
        if not isinstance(q, dict):
            continue
        header = q.get("header") or q.get("question") or ""
        if not isinstance(header, str) or not header.strip():
            continue
        results.append(CompiledDecision(
            ts=ts,
            decision=_truncate(f"asked operator: {header}", 120),
            why=_truncate("AskUserQuestion event during session", 200),
            refs=["tool:AskUserQuestion"],
            source_type="ask-user-question",
            source_tool="AskUserQuestion",
        ))
    return results


def _extract_explicit_decisions_add(input_obj: dict, ts: str) -> CompiledDecision | None:
    """If the session called `agent-continuity decisions add ...` via Bash,
    extract the operator-explicit content from the flags. This is the
    one case where free-form text DOES enter the compile — but it's
    text the operator typed deliberately for the decisions log, so it
    was already going in there anyway."""
    cmd = input_obj.get("command", "")
    if not isinstance(cmd, str):
        return None
    if "decisions" not in cmd or "add" not in cmd:
        return None
    if "agent-continuity" not in cmd and "decisions.sh" not in cmd:
        return None
    # Parse out the flags. Use shlex to handle quoted values.
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    adapter = decision_text = why_text = None
    refs: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--adapter" and i + 1 < len(tokens):
            adapter = tokens[i + 1]; i += 2; continue
        if tok == "--decision" and i + 1 < len(tokens):
            decision_text = tokens[i + 1]; i += 2; continue
        if tok == "--why" and i + 1 < len(tokens):
            why_text = tokens[i + 1]; i += 2; continue
        if tok == "--ref" and i + 1 < len(tokens):
            refs.append(tokens[i + 1]); i += 2; continue
        i += 1
    if not (decision_text and why_text):
        return None
    return CompiledDecision(
        ts=ts,
        decision=_truncate(decision_text, 120),
        why=_truncate(why_text, 200),
        refs=refs + ["tool:Bash", "source:operator-explicit"],
        source_type="explicit-decisions-add",
        source_tool="Bash",
    )


# ────────────────────────────────────────────────────────────────
# Main compile loop

@dataclass
class CompileResult:
    session_id: str
    candidates: list[CompiledDecision] = field(default_factory=list)
    skipped_sensitive: int = 0
    skipped_existing: int = 0
    written: list[str] = field(default_factory=list)  # ids of newly-appended


def _iter_session_events(jsonl_path: pathlib.Path) -> Iterable[dict]:
    """Yield assistant messages (only — user messages don't have tool_use
    blocks in their content arrays)."""
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "assistant":
                    yield entry
    except OSError:
        return


def _modal_cwd(jsonl_path: pathlib.Path) -> str | None:
    """Best-effort: find the dominant cwd across messages in this session."""
    from collections import Counter
    counter: Counter[str] = Counter()
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = e.get("cwd")
                if isinstance(cwd, str):
                    counter[cwd] += 1
    except OSError:
        return None
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def _derive_repo_from_cwd(cwd: str) -> str:
    """Use the cwd's basename as the repo identifier (matches the convention
    used by decisions.sh's _derive_repo when invoked inside a git tree)."""
    return pathlib.Path(cwd).name if cwd else "unknown"


def _existing_decision_ids() -> set[str]:
    """Read decisions.jsonl and return the set of existing ids for dedup."""
    ids: set[str] = set()
    for entry in _iter_entries():
        eid = entry.get("id")
        if isinstance(eid, str):
            ids.add(eid)
    return ids


def _candidate_to_partial(
    cand: CompiledDecision,
    session_id_short: str,
) -> dict:
    """Convert internal CompiledDecision to the partial dict shape
    append_entries_from_transcript_compile expects."""
    return {
        "ts": cand.ts,
        "decision": cand.decision,
        "why": cand.why,
        "refs": cand.refs,
        "author": f"auto:transcript-compile@{session_id_short}",
    }


def _precompute_id(
    partial: dict,
    *,
    adapter: str,
    repo: str,
    session_id: str,
) -> str:
    """Reproduce the id that append_entries_from_transcript_compile would
    assign, so we can check against the existing log for idempotency
    without writing first."""
    auto_ref = f"session:{session_id}"
    body = {
        "schema_version": SCHEMA_VERSION,
        "ts": partial["ts"],
        "adapter": adapter,
        "repo": repo,
        "decision": partial["decision"],
        "why": partial["why"],
        "refs": _merge_auto_ref(partial.get("refs"), auto_ref),
    }
    if "author" in partial:
        body["author"] = partial["author"]
    return _compute_id(body)


def compile_session(
    jsonl_path: pathlib.Path,
    *,
    apply: bool = False,
    no_privacy_filter: bool = False,
) -> CompileResult:
    """Scan a session JSONL, extract heuristic events, optionally append
    new ones to the canonical decisions log.

    Returns a CompileResult with:
      - candidates: every event extracted (before dedup)
      - skipped_sensitive: count filtered by privacy denylist
      - skipped_existing: count already present in decisions log
      - written: ids of entries actually appended (empty if --dry-run)
    """
    session_id = jsonl_path.stem
    session_id_short = session_id.split("-", 1)[0]
    result = CompileResult(session_id=session_id)

    cwd = _modal_cwd(jsonl_path) or ""
    repo = _derive_repo_from_cwd(cwd)

    for entry in _iter_session_events(jsonl_path):
        ts = entry.get("timestamp", "")
        if not isinstance(ts, str) or not ts:
            continue
        # Normalize ts to seconds-precision UTC. Schema accepts any string,
        # but canonicalization keeps ids stable across compiles.
        ts_norm = ts.replace("Z", "+00:00")
        try:
            import datetime as dt
            ts_dt = dt.datetime.fromisoformat(ts_norm)
            ts = ts_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass  # fall back to raw

        msg = entry.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name")
            inp = block.get("input") or {}
            if not isinstance(inp, dict):
                continue

            # Privacy denylist: file_path
            fp = inp.get("file_path")
            if isinstance(fp, str) and _path_is_sensitive(fp):
                if not no_privacy_filter:
                    result.skipped_sensitive += 1
                    continue

            # Privacy denylist: Bash command secrets
            cmd = inp.get("command", "")
            if isinstance(cmd, str) and _bash_command_is_sensitive(cmd):
                if not no_privacy_filter:
                    result.skipped_sensitive += 1
                    continue

            # Try each extractor in confidence order. First match wins
            # per block (a single tool_use can't be both a git commit
            # and a package install).
            cd: CompiledDecision | None = None
            cds: list[CompiledDecision] = []

            if name == "Bash":
                # Try explicit decisions add first (operator-explicit;
                # highest semantic value)
                cd = _extract_explicit_decisions_add(inp, ts)
                if cd is None:
                    cd = _extract_git_commit(inp, ts, session_id)
                if cd is None:
                    cd = _extract_release_or_tag(inp, ts, session_id)
                if cd is None:
                    cd = _extract_package_install(inp, ts, session_id)
            elif name in ("Edit", "Write"):
                cd = _extract_file_edit(name, inp, ts, cwd)
            elif name == "AskUserQuestion":
                cds = _extract_ask_user_question(inp, ts)

            if cd is not None:
                result.candidates.append(cd)
            if cds:
                result.candidates.extend(cds)

    # Idempotency: skip candidates whose precomputed id is already in
    # the decisions log.
    existing_ids = _existing_decision_ids()
    fresh_partials: list[dict] = []
    for cand in result.candidates:
        partial = _candidate_to_partial(cand, session_id_short)
        cid = _precompute_id(
            partial,
            adapter="claude",
            repo=repo,
            session_id=session_id,
        )
        if cid in existing_ids:
            result.skipped_existing += 1
            continue
        fresh_partials.append(partial)
        existing_ids.add(cid)  # avoid intra-batch dedup blowing up

    if apply and fresh_partials:
        written_ids = append_entries_from_transcript_compile(
            fresh_partials,
            adapter="claude",
            repo=repo,
            session_id=session_id,
        )
        result.written = written_ids

    return result
