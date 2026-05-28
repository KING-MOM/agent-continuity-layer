#!/usr/bin/env python3
"""context snapshot generator for agent-continuity-layer (M7.0).

Continuity primitive: context recovery.

Emits a single JSON object that orients a fresh agent in under 60 seconds:
project identity, current milestone state, what's pending review, what's in
flight, the charter non-goals, where to read more, and an operator-maintained
next safe action. All but next_safe_action is derived from authoritative
sources (git, CHARTER.md, docs/roadmap.md, the worker queue, trust policy)
and regenerated on every invocation.

Schema: core/schemas/context-snapshot.schema.json (v1.0).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = pathlib.Path.home()

CHARTER_PATH = REPO_ROOT / "CHARTER.md"
ROADMAP_PATH = REPO_ROOT / "docs" / "roadmap.md"
PINNED_PATH = REPO_ROOT / "core" / "context-pinned.json"
SNAPSHOT_JSON_PATH = REPO_ROOT / "core" / "context-snapshot.json"
SNAPSHOT_MD_PATH = REPO_ROOT / "core" / "context-snapshot.md"

_XDG_CACHE_HOME = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache"))
_XDG_CONFIG_HOME = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
QUEUE_ROOT = _XDG_CACHE_HOME / "agent-continuity" / "queue"
# Matches scripts/_doctor.py check_queue states. Order is informational.
QUEUE_STATES = (
    "queued", "claimed", "running", "awaiting-approval",
    "completed", "rejected", "failed", "cancelled",
)
OPEN_STATES = ("queued", "claimed", "running", "awaiting-approval")

POLICY_PATH = _XDG_CONFIG_HOME / "agent-continuity" / "trust-policy.json"

# Gloss of CHARTER.md's milestone rule, kept short for the 60-second budget.
# Source of truth is CHARTER.md "Milestone Rule" + "Architecture Rules" — the
# snapshot is meant to point readers there, not replace it.
MILESTONE_RULE = (
    "Every milestone must strengthen at least one charter continuity "
    "primitive; delegation-only work belongs in adapters."
)

# Milestone-major -> charter primitive. Inferred mapping; null if not a
# charter primitive (M0 scaffold, M1 doctor). Refined later via commit
# trailers if inference gets messy.
PRIMITIVE_BY_MILESTONE_MAJOR: dict[str, str | None] = {
    "M0": None,
    "M1": None,
    "M2": "context recovery",
    "M3": "context recovery",
    "M4": "handoff ledger",
    "M5": "adapter portability",
    "M6": "context recovery",
    "M7": "context recovery",
    "M8": "decision log",
    "M9": "adapter portability",
    "M10": "history",
    "M11": "adapter portability",
}

CHARTER_PRIMITIVES: frozenset[str] = frozenset({
    "project registry", "context recovery", "decision log", "history",
    "trust policy", "handoff ledger", "artifact memory", "adapter portability",
})

# Matches M-tags in commit subjects with up to a patch level:
#   M6, M6.2, M6.1.1, M5.2a, M5.2a.1, M7.0.1, etc.
# Groups: 1=major, 2=minor, 3=letter-suffix, 4=patch.
# M7.0.2 fix: original M_TAG_RE stopped after the letter-suffix group,
# silently dropping the trailing ".N" patch. That made "M7.0.1" extract as
# "M7.0", which was harmless until M7.1's doctor display surfaced it.
M_TAG_RE = re.compile(r"\bM(\d+)(?:\.(\d+)([a-z]\d*)?(?:\.(\d+))?)?\b")


def _run(cmd: list[str], cwd: pathlib.Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd or REPO_ROOT, timeout=5,
        )
        return p.returncode, p.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 127, ""


def _git_head_sha() -> str | None:
    rc, out = _run(["git", "rev-parse", "HEAD"])
    return out.strip() if rc == 0 and out.strip() else None


def _git_branch() -> str | None:
    rc, out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip() if rc == 0 and out.strip() else None


def _git_recent_commits(n: int = 5) -> list[dict[str, Any]]:
    rc, out = _run(["git", "log", f"-{n}", "--format=%H%x09%s"])
    if rc != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        rows.append({"sha": sha[:7], "subject": subject})
    return rows


def _extract_milestone_tag(subject: str) -> tuple[str | None, int | None]:
    m = M_TAG_RE.search(subject)
    if not m:
        return None, None
    major = int(m.group(1))
    minor = m.group(2)
    suffix = m.group(3) or ""
    patch = m.group(4)
    if minor is None:
        return f"M{major}", major
    tag = f"M{major}.{minor}{suffix}"
    if patch is not None:
        tag += f".{patch}"
    return tag, major


def _primitive_for(tag: str | None) -> str | None:
    if not tag:
        return None
    major = "M" + tag[1:].split(".", 1)[0]
    p = PRIMITIVE_BY_MILESTONE_MAJOR.get(major)
    return p if p in CHARTER_PRIMITIVES else None


def _last_completed_milestone() -> tuple[str | None, int | None]:
    """Highest M-tag in any commit subject. Ranks (major, minor, suffix-rank)."""
    rc, out = _run(["git", "log", "--format=%s"])
    if rc != 0:
        return None, None
    best_tag: str | None = None
    best_major: int | None = None
    # Order: (major, minor, suffix, patch). Empty suffix sorts before any
    # letter ("" < "a"); -1 minor/patch sort before any present minor/patch.
    best_key: tuple[int, int, str, int] = (-1, -1, "", -1)
    for line in out.splitlines():
        m = M_TAG_RE.search(line)
        if not m:
            continue
        major = int(m.group(1))
        minor = int(m.group(2)) if m.group(2) else -1
        suffix = m.group(3) or ""
        patch = int(m.group(4)) if m.group(4) else -1
        key = (major, minor, suffix, patch)
        if key > best_key:
            best_key = key
            tag, _ = _extract_milestone_tag(line)
            best_tag = tag
            best_major = major
    return best_tag, best_major


def _next_major_from_roadmap(last_major: int | None) -> dict[str, Any]:
    """Find first ### M{n} heading in docs/roadmap.md where n > last_major.

    Returns {present, tag, label}. present=False when no later major is
    listed (or roadmap absent / last_major unknown).

    The roadmap intentionally tracks majors only — it would grow stale
    fast if it inlined every sub-slice. The operator's slice-level intent
    for the immediate next step (e.g. 'M7.1 doctor freshness check') lives
    in next_safe_action, sourced from core/context-pinned.json. M7.0
    originally named this field 'next_proposed' and elided the major-vs-
    slice distinction; M7.0.1 renamed it after a fresh-agent test showed
    the snapshot pointing at M8 while the real next step was M7.1."""
    blank = {"present": False, "tag": "", "label": ""}
    if last_major is None or not ROADMAP_PATH.exists():
        return blank
    head_re = re.compile(r"^###\s+M(\d+)\b(.*)$")
    try:
        text = ROADMAP_PATH.read_text(encoding="utf-8")
    except OSError:
        return blank
    for line in text.splitlines():
        m = head_re.match(line)
        if not m:
            continue
        n = int(m.group(1))
        if n > last_major:
            label = m.group(2).strip().lstrip("-").strip()
            return {"present": True, "tag": f"M{n}", "label": label}
    return blank


def _charter_one_line() -> str:
    if not CHARTER_PATH.exists():
        return ""
    try:
        text = CHARTER_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            in_section = (line == "## Primary Goal")
            continue
        if in_section and line:
            return line
    return ""


def _charter_non_goals() -> list[str]:
    if not CHARTER_PATH.exists():
        return []
    try:
        text = CHARTER_PATH.read_text(encoding="utf-8")
    except OSError:
        return []
    in_section = False
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            if in_section:
                break
            in_section = (line == "## Non-Goals")
            continue
        if in_section and line.startswith("- "):
            out.append(line[2:].strip())
    return out


def _queue_snapshot() -> dict[str, Any]:
    counts: dict[str, int] = {s: 0 for s in QUEUE_STATES}
    open_tasks: dict[str, list[str]] = {s: [] for s in OPEN_STATES}
    if not QUEUE_ROOT.is_dir():
        return {"queue_present": False, "queue_counts": counts, "open_tasks": open_tasks}
    for state in QUEUE_STATES:
        d = QUEUE_ROOT / state
        if not d.is_dir():
            continue
        ids: list[str] = []
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix == ".json" and entry.is_file():
                counts[state] += 1
                ids.append(entry.stem)
        if state in open_tasks:
            open_tasks[state] = ids
    return {"queue_present": True, "queue_counts": counts, "open_tasks": open_tasks}


def _trust_snapshot() -> dict[str, Any]:
    blank = {
        "policy_present": False,
        "grants_count": 0,
        "default_policy_summary": "absent",
        "soonest_expiry": None,
    }
    if not POLICY_PATH.exists():
        return blank
    try:
        data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**blank, "policy_present": True, "default_policy_summary": "unreadable"}

    repos = data.get("repos") or []
    default = data.get("default") or {}
    default_kinds = sorted(default.get("allow_kinds") or [])
    if not default_kinds:
        summary = "default denies all"
    else:
        summary = f"default allows {len(default_kinds)} kind(s): {', '.join(default_kinds)}"

    soonest: str | None = None
    for repo in repos:
        policy = repo.get("policy") or {}
        exp = policy.get("expires_at")
        if isinstance(exp, str) and (soonest is None or exp < soonest):
            soonest = exp

    return {
        "policy_present": True,
        "grants_count": len(repos),
        "default_policy_summary": summary,
        "soonest_expiry": soonest,
    }


def _pinned_next_safe_action() -> str:
    if not PINNED_PATH.exists():
        return ""
    try:
        data = json.loads(PINNED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    nsa = data.get("next_safe_action")
    return nsa.strip() if isinstance(nsa, str) else ""


def _display_repo_path() -> str:
    """Keep committed snapshots useful without leaking the operator username."""
    try:
        return "~/" + str(REPO_ROOT.relative_to(HOME))
    except ValueError:
        return str(REPO_ROOT)


def build_snapshot() -> dict[str, Any]:
    recent = _git_recent_commits(5)
    for c in recent:
        tag, _ = _extract_milestone_tag(c["subject"])
        c["milestone"] = tag
        c["primitive"] = _primitive_for(tag)

    last_tag, last_major = _last_completed_milestone()

    return {
        "schema_version": "1.0",
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_head_sha": _git_head_sha(),
        "identity": {
            "name": "agent-continuity-layer",
            "charter_one_line": _charter_one_line(),
            "repo_path": _display_repo_path(),
            "branch": _git_branch() or "",
        },
        "milestone": {
            "last_completed": last_tag,
            "last_completed_primitive": _primitive_for(last_tag),
            "next_major_milestone": _next_major_from_roadmap(last_major),
            "milestone_rule": MILESTONE_RULE,
        },
        "navigation": {
            "charter": "CHARTER.md",
            "roadmap": "docs/roadmap.md",
            "architecture": "docs/architecture.md",
            "handoff_vs_continuity": "docs/handoff-vs-continuity.md",
            "milestone_template": "docs/milestone-template.md",
        },
        "non_goals": _charter_non_goals(),
        "work_in_flight": _queue_snapshot(),
        "trust": _trust_snapshot(),
        "recent_activity": recent,
        "next_safe_action": _pinned_next_safe_action(),
    }


def render_markdown(snap: dict[str, Any]) -> str:
    L: list[str] = []
    add = L.append

    add("# Project Context Snapshot")
    add("")
    add(f"_Generated: {snap['generated_at']} from {snap['source_head_sha'] or 'unknown HEAD'}_")
    add("")
    add("> This is a generated snapshot. Do not edit by hand — run "
        "`scripts/context.sh --write` to refresh. The one operator-maintained "
        "field is `next_safe_action`, sourced from `core/context-pinned.json`.")
    add("")

    iden = snap["identity"]
    add("## Identity")
    add("")
    add(f"- **Project**: {iden['name']}")
    add(f"- **Charter**: {iden['charter_one_line']}")
    add(f"- **Repo**: `{iden['repo_path']}`")
    add(f"- **Branch**: `{iden['branch']}`")
    add(f"- **HEAD**: `{snap['source_head_sha'] or 'unknown'}`")
    add("")

    ms = snap["milestone"]
    add("## Milestone State")
    add("")
    lc = ms["last_completed"] or "unknown"
    lcp = f" — primitive: _{ms['last_completed_primitive']}_" if ms["last_completed_primitive"] else ""
    add(f"- **Last completed**: {lc}{lcp}")
    nm = ms["next_major_milestone"]
    if nm["present"]:
        label = f" — {nm['label']}" if nm["label"] else ""
        add(f"- **Next major milestone** (per roadmap): {nm['tag']}{label}")
    else:
        add("- **Next major milestone** (per roadmap): (none listed beyond last_completed)")
    add(f"- **Milestone rule**: {ms['milestone_rule']}")
    add("")
    add("_The roadmap tracks majors only. The next concrete sub-slice (e.g. M7.1) lives in **Next Safe Action** below, sourced from `core/context-pinned.json`._")
    add("")

    add("## Next Safe Action")
    add("")
    nsa = snap["next_safe_action"]
    if nsa:
        add(nsa)
    else:
        add("_(operator has not set `next_safe_action` in `core/context-pinned.json`)_")
    add("")

    add("## Navigation")
    add("")
    # core/context-snapshot.md lives one level deep, so links resolve via "../".
    # JSON consumers keep repo-root paths (snap["navigation"][k]); only the
    # human render rewrites them. Don't drift these out of sync — if the
    # snapshot file ever moves out of core/, update the prefix here.
    for key, path in snap["navigation"].items():
        add(f"- [{path}](../{path}) — {key.replace('_', ' ')}")
    add("")

    add("## Non-Goals")
    add("")
    add("_Verbatim from CHARTER.md — what this project is deliberately not:_")
    add("")
    for ng in snap["non_goals"]:
        add(f"- {ng}")
    add("")

    wif = snap["work_in_flight"]
    add("## Work In Flight")
    add("")
    if not wif["queue_present"]:
        add("_Queue directory not present on this host._")
    else:
        counts = wif["queue_counts"]
        add("| state | count |")
        add("|---|---|")
        for state, n in counts.items():
            add(f"| {state} | {n} |")
        add("")
        open_tasks = wif["open_tasks"]
        any_open = any(open_tasks.get(s) for s in open_tasks)
        if any_open:
            add("Open task IDs:")
            add("")
            for state, ids in open_tasks.items():
                if ids:
                    add(f"- **{state}**: {', '.join(ids)}")
        else:
            add("_No open tasks._")
    add("")

    tr = snap["trust"]
    add("## Trust")
    add("")
    if not tr["policy_present"]:
        add("_No trust policy on this host._")
    else:
        add(f"- **Grants**: {tr['grants_count']}")
        add(f"- **Default policy**: {tr['default_policy_summary']}")
        add(f"- **Soonest expiry**: {tr['soonest_expiry'] or 'none'}")
    add("")

    add("## Recent Activity")
    add("")
    if snap["recent_activity"]:
        add("| sha | milestone | primitive | subject |")
        add("|---|---|---|---|")
        for c in snap["recent_activity"]:
            tag = c["milestone"] or "—"
            prim = c["primitive"] or "—"
            subj = c["subject"].replace("|", "\\|")
            add(f"| `{c['sha']}` | {tag} | {prim} | {subj} |")
    else:
        add("_No commits found._")
    add("")

    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project context snapshot generator (M7.0).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true",
                      help="emit JSON snapshot to stdout (default)")
    mode.add_argument("--md", action="store_true",
                      help="emit markdown snapshot to stdout")
    mode.add_argument("--write", action="store_true",
                      help="write both core/context-snapshot.json and core/context-snapshot.md")
    args = parser.parse_args(argv)

    snap = build_snapshot()

    if args.write:
        SNAPSHOT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_JSON_PATH.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
        SNAPSHOT_MD_PATH.write_text(render_markdown(snap), encoding="utf-8")
        print(f"wrote {SNAPSHOT_JSON_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
        print(f"wrote {SNAPSHOT_MD_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 0

    if args.md:
        sys.stdout.write(render_markdown(snap))
    else:
        sys.stdout.write(json.dumps(snap, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
