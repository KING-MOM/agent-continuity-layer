#!/usr/bin/env python3
"""decisions log — append-only cross-agent decision writeback (M8.0).

Continuity primitive: decision log.

Records *why* a non-obvious choice was made so a different agent — on a
different model, in a different session, on a different device — can
rely on it without re-asking the operator. M7 made context recoverable;
M8 makes the reasoning behind context entries durable.

File layout:
  $XDG_STATE_HOME/agent-continuity/decisions.jsonl   (default ~/.local/state/...)
  $XDG_STATE_HOME/agent-continuity/decisions.jsonl.lock

Each line of decisions.jsonl is a JSON object validated against
core/schemas/decision-entry.schema.json on every append. No edit/delete
command exists. The only way to mutate the log is to append a new entry;
the only way to remove entries is the destructive `rm` outside this CLI.

Append safety: a single per-process lock file. Acquire with exclusive
create, write, release. If a writer crashes mid-add, the lock file
persists and subsequent adds will block until it's manually cleared
(`rm decisions.jsonl.lock`). Stale-lock auto-recovery is intentionally
deferred — for memory infrastructure, a noisy block beats a silent
double-write race.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "core" / "schemas" / "decision-entry.schema.json"

ADAPTERS: tuple[str, ...] = ("claude", "codex", "openclaw", "human", "chatgpt", "gemini", "grok", "kimi")
SCHEMA_VERSION = "1.0"

# XDG_STATE_HOME per the XDG Base Directory Spec: durable user state,
# distinct from cache (transient) and config (machine-edited). Honor the
# env var if set; default to ~/.local/state otherwise.
_XDG_STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
STATE_DIR = _XDG_STATE_HOME / "agent-continuity"
DECISIONS_PATH = STATE_DIR / "decisions.jsonl"
LOCK_PATH = STATE_DIR / "decisions.jsonl.lock"
# M8.4 compaction output. Derived view, regeneratable from DECISIONS_PATH.
# Never modifies the canonical source; safe to delete and rebuild.
COMPACTED_PATH = STATE_DIR / "decisions.compacted.jsonl"

# M8.4 defaults
COMPACT_DEFAULT_KEEP = 20
COMPACT_DEFAULT_AUTHOR = "auto:scripts/decisions.sh-compact"

LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.05


# ---------- locking ----------

def _acquire_lock(timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
    """Atomic exclusive-create lock. Blocks up to `timeout` seconds.

    Raises TimeoutError on timeout. Stale locks are NOT auto-cleaned —
    operator must `rm decisions.jsonl.lock` if a writer crashed mid-add.
    Trade-off: simpler implementation, no risk of stomping a slow writer."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            os.close(fd)
            return
        except FileExistsError:
            if time.monotonic() >= deadline:
                holder = "unknown"
                try:
                    holder = LOCK_PATH.read_text(encoding="utf-8").strip() or "unknown"
                except OSError:
                    pass
                raise TimeoutError(
                    f"could not acquire {LOCK_PATH} within {timeout}s "
                    f"(held by pid {holder}; rm the lock file if the writer crashed)"
                )
            time.sleep(LOCK_POLL_SECONDS)


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


# ---------- id + validation ----------

def _compute_id(entry: dict[str, Any]) -> str:
    """sha256 hex of the canonical body (entry with id removed, sorted-keys
    JSON, no whitespace separators). Deterministic; two identical decisions
    get the same id (intentional dedup behavior)."""
    body = {k: v for k, v in entry.items() if k != "id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_entry(entry: dict[str, Any]) -> list[str]:
    """Field-by-field validation against the M8.0 contract. Kept inline
    (not schema-driven) so this script doesn't depend on the doctor's
    JSON Schema validator. M8.1's doctor check will validate against
    the canonical schema in core/schemas/decision-entry.schema.json —
    defense in depth for the same contract.

    If you change required fields here, update the schema file too."""
    errors: list[str] = []
    if not isinstance(entry, dict):
        return [f"entry is not an object: {type(entry).__name__}"]

    required = ("schema_version", "id", "ts", "adapter", "repo", "decision", "why", "refs")
    allowed = required + ("author",)
    for k in required:
        if k not in entry:
            errors.append(f"missing required field {k!r}")
    for k in entry.keys():
        if k not in allowed:
            errors.append(f"unknown field {k!r}")

    if entry.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION!r}, got {entry.get('schema_version')!r}"
        )
    if "id" in entry and not isinstance(entry["id"], str):
        errors.append("id must be a string")
    if "ts" in entry and not isinstance(entry["ts"], str):
        errors.append("ts must be a string")
    if "adapter" in entry and entry["adapter"] not in ADAPTERS:
        errors.append(f"adapter must be one of {ADAPTERS!r}, got {entry.get('adapter')!r}")
    if "author" in entry and not isinstance(entry["author"], str):
        errors.append("author must be a string when present")
    if "repo" in entry and not isinstance(entry["repo"], str):
        errors.append("repo must be a string")
    if "decision" in entry:
        if not isinstance(entry["decision"], str) or not entry["decision"].strip():
            errors.append("decision must be a non-empty string")
    if "why" in entry:
        if not isinstance(entry["why"], str) or not entry["why"].strip():
            errors.append("why must be a non-empty string")
    if "refs" in entry:
        refs = entry["refs"]
        if not isinstance(refs, list):
            errors.append("refs must be an array")
        else:
            for i, r in enumerate(refs):
                if not isinstance(r, str):
                    errors.append(f"refs[{i}] must be a string")
    return errors


# ---------- repo derivation ----------

def _derive_repo() -> str:
    """basename(git rev-parse --show-toplevel) from cwd, or 'unknown' if
    not in a git repo. Operators override with --repo on the CLI."""
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    if p.returncode != 0 or not p.stdout.strip():
        return "unknown"
    return Path(p.stdout.strip()).name or "unknown"


# ---------- worker integration (M8.3) ----------

class WorkerDecisionDraftError(ValueError):
    """Raised when a worker-submitted decision draft fails validation.

    Caller (typically _worker.py cmd_submit) catches this, surfaces the
    message to the operator, and refuses the submit. No decisions are
    appended when this is raised — validation runs across the whole batch
    before any write."""


def _validate_worker_decision_draft(d: Any, index: int) -> list[str]:
    """Validate a WorkerDecisionDraft shape: the subset of decision-entry
    fields a worker provides. Required: decision, why. Optional: refs (list
    of strings), author (string). Any other field is rejected.

    Mirrors core/schemas/worker-result.schema.json:$defs/WorkerDecisionDraft.
    If you change required/allowed fields here, update the schema too."""
    errors: list[str] = []
    prefix = f"decisions[{index}]"
    if not isinstance(d, dict):
        return [f"{prefix}: not an object ({type(d).__name__})"]

    allowed = ("decision", "why", "refs", "author")
    required = ("decision", "why")
    for k in required:
        if k not in d:
            errors.append(f"{prefix}: missing required field {k!r}")
    for k in d.keys():
        if k not in allowed:
            errors.append(
                f"{prefix}: unknown field {k!r} (allowed: {list(allowed)})"
            )
    if "decision" in d:
        if not isinstance(d["decision"], str) or not d["decision"].strip():
            errors.append(f"{prefix}: decision must be a non-empty string")
    if "why" in d:
        if not isinstance(d["why"], str) or not d["why"].strip():
            errors.append(f"{prefix}: why must be a non-empty string")
    if "refs" in d:
        if not isinstance(d["refs"], list):
            errors.append(f"{prefix}: refs must be an array")
        else:
            for i, r in enumerate(d["refs"]):
                if not isinstance(r, str):
                    errors.append(f"{prefix}: refs[{i}] must be a string")
    if "author" in d:
        if not isinstance(d["author"], str):
            errors.append(f"{prefix}: author must be a string when present")
    return errors


def _merge_auto_ref(existing_refs: list[Any] | None, auto_ref: str) -> list[str]:
    """Prepend `auto_ref` after deduping any existing matching ref.

    The caller is the canonical authority on the auto-injected ref (e.g.
    'task:<id>' from worker submit, 'bundle:<id>' from bundle ingest); the
    ref must be present and at index 0 so log readers find it quickly.
    If the caller already included the same ref, we keep order but ensure
    no duplicate. Non-string entries are filtered (defense in depth;
    _validate_worker_decision_draft already rejects them earlier).

    M9.1: renamed from _merge_task_ref + parameterized so bundle-driven
    appends can inject 'bundle:<id>' instead of 'task:<id>'."""
    existing = [r for r in (existing_refs or []) if isinstance(r, str)]
    return [auto_ref] + [r for r in existing if r != auto_ref]


def _append_entries_internal(
    partials: Any,
    *,
    adapter: str,
    repo: str,
    auto_ref: str,
) -> list[str]:
    """Shared atomic-batch validator + writer. Used by both
    append_entries_from_worker (task: refs) and append_entries_from_bundle
    (bundle: refs). All-or-nothing: validates the whole batch before
    acquiring the lock; raises WorkerDecisionDraftError on any invalid
    draft (no partial writes).

    Synthesized fields per entry:
        schema_version  '1.0'
        id              sha256 of canonical body
        ts              UTC at write time
        adapter         caller-provided
        repo            caller-provided
        refs            [auto_ref] + caller's refs (deduped)

    Returns the list of sha256 ids in the same order as `partials`.
    Empty `partials` returns [] without acquiring the lock."""
    if partials is None or partials == []:
        return []
    if not isinstance(partials, list):
        raise WorkerDecisionDraftError(
            f"decisions must be a list, got {type(partials).__name__}"
        )

    # Validate + synthesize the whole batch before any write.
    entries: list[dict[str, Any]] = []
    for i, partial in enumerate(partials):
        draft_errs = _validate_worker_decision_draft(partial, i)
        if draft_errs:
            raise WorkerDecisionDraftError("; ".join(draft_errs))

        entry: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "adapter": adapter,
            "repo": repo,
            "decision": partial["decision"],
            "why": partial["why"],
            "refs": _merge_auto_ref(partial.get("refs"), auto_ref),
        }
        if "author" in partial:
            entry["author"] = partial["author"]
        entry["id"] = _compute_id(entry)

        full_errs = _validate_entry(entry)
        if full_errs:
            raise WorkerDecisionDraftError(
                f"decisions[{i}] synthesized entry failed validation: "
                + "; ".join(full_errs)
            )
        entries.append(entry)

    # All valid; write under a single lock acquisition.
    _acquire_lock()
    try:
        with open(DECISIONS_PATH, "a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    finally:
        _release_lock()

    return [e["id"] for e in entries]


def append_entries_from_worker(
    partials: Any,
    *,
    adapter: str,
    repo: str,
    task_id: str,
) -> list[str]:
    """M8.3: worker-result decision writeback. Auto-injects 'task:<task_id>'
    as the first ref on each entry. Thin wrapper over _append_entries_internal."""
    return _append_entries_internal(
        partials,
        adapter=adapter,
        repo=repo,
        auto_ref=f"task:{task_id}",
    )


def append_entries_from_bundle(
    partials: Any,
    *,
    adapter: str,
    repo: str,
    bundle_id: str,
) -> list[str]:
    """M9.1: bundle-ingest decision writeback. Auto-injects 'bundle:<bundle_id>'
    as the first ref on each entry. Used when a bundle's adapter-to-layer
    return carries append_decisions[] that are NOT tied to a worker submit.
    Same validate+lock+append semantics as append_entries_from_worker.

    For bundle submits, append_entries_from_worker still runs (transparently,
    via the worker.sh submit path) so task: ref injection is preserved
    where the decision is genuinely task-attributed."""
    return _append_entries_internal(
        partials,
        adapter=adapter,
        repo=repo,
        auto_ref=f"bundle:{bundle_id}",
    )


# ---------- commands ----------

def cmd_add(args: argparse.Namespace) -> int:
    # M14.0: auto-register the project for cwd if it has a git remote.
    # No-op when cwd is not a git repo. Side effect (one stderr line on
    # new registration) is intentional — the operator should see when
    # registry state changes even if they didn't type `project add`.
    try:
        from _project import ensure_project_registered
        ensure_project_registered()
    except Exception:  # noqa: BLE001 — auto-register must never block decision append
        pass

    if args.repo is not None:
        repo = args.repo
    else:
        repo = _derive_repo()

    entry: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adapter": args.adapter,
        "repo": repo,
        "decision": args.decision,
        "why": args.why,
        "refs": list(args.ref) if args.ref else [],
    }
    if args.author:
        entry["author"] = args.author

    entry["id"] = _compute_id(entry)

    errors = _validate_entry(entry)
    if errors:
        print("error: decision entry failed validation:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    line = json.dumps(entry, ensure_ascii=False) + "\n"

    try:
        _acquire_lock()
    except TimeoutError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        with open(DECISIONS_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    finally:
        _release_lock()

    # Convention: id-only on stdout (pipeable); human summary on stderr.
    print(entry["id"])
    print(
        f"appended decision {entry['id'][:7]} (adapter={entry['adapter']}, "
        f"repo={entry['repo']}) to {DECISIONS_PATH}",
        file=sys.stderr,
    )
    return 0


def _iter_entries() -> list[dict[str, Any]]:
    """Read all entries from disk. M8.0 loads then sorts; streaming with
    sort is impossible. Compaction (M8.4) will keep file size bounded."""
    if not DECISIONS_PATH.exists():
        return []
    entries: list[dict[str, Any]] = []
    with open(DECISIONS_PATH, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(
                    f"warning: malformed JSONL at {DECISIONS_PATH}:{lineno}: {e}",
                    file=sys.stderr,
                )
    return entries


def _build_compaction_summary(
    older: list[dict[str, Any]],
    author: str,
) -> dict[str, Any]:
    """Synthesize one decision-entry summarizing `older`. The summary is
    schema-valid (passes _validate_entry) and points back to every
    summarized id via refs (`decision:<sha256>`), so a future agent
    can trace from the compacted view to any individual entry in the
    canonical decisions.jsonl.

    Adapter is 'human' (the operator initiated the compaction); author
    defaults to 'auto:scripts/decisions.sh-compact' so machine-generated
    content is clearly distinguishable from a hand-written 'human'
    decision."""
    if not older:
        raise ValueError("_build_compaction_summary requires at least one older entry")

    sorted_older = sorted(older, key=lambda e: e.get("ts", "") or "")
    oldest_ts = sorted_older[0].get("ts") or "?"
    newest_ts = sorted_older[-1].get("ts") or "?"
    adapters = sorted({
        e.get("adapter") for e in older
        if isinstance(e.get("adapter"), str)
    })
    repos = sorted({
        e.get("repo") for e in older
        if isinstance(e.get("repo"), str)
    })
    refs = [
        f"decision:{e['id']}"
        for e in sorted_older
        if isinstance(e.get("id"), str)
    ]

    adapters_str = ", ".join(adapters) if adapters else "(none)"
    repos_str = ", ".join(repos) if repos else "(none)"
    entry: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "adapter": "human",
        "author": author or COMPACT_DEFAULT_AUTHOR,
        "repo": "global",
        "decision": (
            f"Rolling-summary entry for {len(older)} older decisions "
            f"({oldest_ts} -> {newest_ts}). Adapters: {adapters_str}; "
            f"repos: {repos_str}. Per-decision sha256 ids in refs[]."
        ),
        "why": (
            "M8.4 compaction: bounds the visible-recent-history view in the "
            "derived decisions.compacted.jsonl. The canonical decisions.jsonl "
            "is never modified — refs[] points back to each summarized id so "
            "any individual decision remains reachable."
        ),
        "refs": refs,
    }
    entry["id"] = _compute_id(entry)

    # Defense in depth: ensure the synthesized entry is schema-valid before
    # returning. If it isn't, the bug is here, not in the writer.
    errs = _validate_entry(entry)
    if errs:
        raise ValueError(
            "internal: compaction summary failed validation: " + "; ".join(errs)
        )
    return entry


def cmd_compact(args: argparse.Namespace) -> int:
    """M8.4: write a compacted derived view of the decisions log.

    Output: [summary, ...recent_N] in chronological order. Recent N
    entries pass through verbatim; the summary represents all older
    entries with refs pointing back to each summarized id.

    The canonical decisions.jsonl is NEVER modified — only the derived
    file at COMPACTED_PATH (defaults to
    $XDG_STATE_HOME/agent-continuity/decisions.compacted.jsonl) is
    written. --dry-run prints the would-be output to stdout without
    touching disk.

    No auto-compaction; the operator decides when to run this. Doctor's
    M8.1 check reports size + count to inform that decision."""
    entries = _iter_entries()
    if not entries:
        print("(no decisions to compact)", file=sys.stderr)
        return 0

    entries.sort(key=lambda e: e.get("ts", "") or "")
    keep_n = args.keep

    if len(entries) <= keep_n:
        print(
            f"(nothing to compact: {len(entries)} entries, keep={keep_n})",
            file=sys.stderr,
        )
        return 0

    older = entries[: len(entries) - keep_n]
    recent = entries[len(entries) - keep_n:]
    summary = _build_compaction_summary(older, args.author)
    output_entries = [summary, *recent]

    output_path = Path(args.output) if args.output else COMPACTED_PATH

    if args.dry_run:
        for e in output_entries:
            sys.stdout.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(
            f"(--dry-run: would write {len(output_entries)} entries "
            f"(1 summary + {len(recent)} recent verbatim, summarizing "
            f"{len(older)} older) to {output_path})",
            file=sys.stderr,
        )
        return 0

    # Atomic write: tmp + rename, so a crash mid-write doesn't leave a
    # partially-written derived file in place.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            for e in output_entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp_path, output_path)
    except OSError as e:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        print(f"error: compacted file write failed: {e}", file=sys.stderr)
        return 1

    # Convention (matches cmd_add): id-only on stdout (pipeable); human
    # summary on stderr.
    print(summary["id"])
    print(
        f"wrote {len(output_entries)} entries to {output_path} "
        f"(1 summary covering {len(older)} older + {len(recent)} recent verbatim)",
        file=sys.stderr,
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    entries = _iter_entries()
    if args.repo is not None:
        entries = [e for e in entries if e.get("repo") == args.repo]
    if args.adapter is not None:
        entries = [e for e in entries if e.get("adapter") == args.adapter]

    # Newest first. Sort is stable; ties keep file order.
    entries.sort(key=lambda e: e.get("ts", ""), reverse=True)

    if args.limit and args.limit > 0:
        entries = entries[: args.limit]

    if args.json:
        for e in entries:
            print(json.dumps(e, ensure_ascii=False))
        return 0

    if not entries:
        print(f"(no decisions in {DECISIONS_PATH})", file=sys.stderr)
        return 0

    for e in entries:
        eid = (e.get("id") or "")[:7] or "—"
        ts = e.get("ts") or "—"
        adapter = e.get("adapter") or "—"
        repo = e.get("repo") or "—"
        author = f" by {e['author']}" if e.get("author") else ""
        print(f"{ts}  [{adapter}{author}]  repo={repo}  id={eid}")
        print(f"  decision: {e.get('decision','')}")
        print(f"  why     : {e.get('why','')}")
        refs = e.get("refs") or []
        if refs:
            print(f"  refs    : {', '.join(refs)}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-agent decision log (M8.0). Append-only writeback so a "
            "different agent — different model, different session, different "
            "device — can rely on the reasoning behind prior choices."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    add_p = sub.add_parser("add", help="append a new decision entry")
    add_p.add_argument("--adapter", required=True, choices=ADAPTERS,
                       help="source adapter for this decision")
    add_p.add_argument("--decision", required=True,
                       help="1-2 sentence statement of what was decided")
    add_p.add_argument("--why", required=True,
                       help="1-2 sentence statement of the constraint/driver")
    add_p.add_argument("--author",
                       help="optional free-form author (e.g. 'operator')")
    add_p.add_argument("--repo",
                       help="override repo scope; default: basename(git toplevel) "
                            "or 'unknown'. Use 'global' for cross-repo decisions.")
    add_p.add_argument("--ref", action="append", default=[],
                       help="add a free-form ref (repeatable); e.g. task:task-12, "
                            "commit:abc1234, doc:CHARTER.md, M7.2")

    list_p = sub.add_parser("list", help="list decision entries (newest first)")
    list_p.add_argument("--repo", help="filter by repo")
    list_p.add_argument("--adapter", choices=ADAPTERS, help="filter by adapter")
    list_p.add_argument("--limit", type=int, default=0,
                        help="cap to last N entries (0 = no limit)")
    list_p.add_argument("--json", action="store_true",
                        help="emit one JSON object per line instead of human render")

    compact_p = sub.add_parser(
        "compact",
        help=(
            "write a compacted derived view: recent N verbatim + one rolling-"
            "summary for older entries. NEVER modifies the canonical log."
        ),
    )
    compact_p.add_argument(
        "--keep", type=int, default=COMPACT_DEFAULT_KEEP,
        help=f"keep last N entries verbatim (default {COMPACT_DEFAULT_KEEP})",
    )
    compact_p.add_argument(
        "--dry-run", action="store_true",
        help="print the would-be compacted view to stdout; write nothing",
    )
    compact_p.add_argument(
        "--output",
        help=f"override output path (default {COMPACTED_PATH})",
    )
    compact_p.add_argument(
        "--author", default=COMPACT_DEFAULT_AUTHOR,
        help=f"summary author tag (default {COMPACT_DEFAULT_AUTHOR!r})",
    )

    args = parser.parse_args(argv)
    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "compact":
        return cmd_compact(args)
    parser.print_help(sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
