#!/usr/bin/env python3
"""worker.py — agent-continuity worker-task queue.

Subcommands: enqueue, list, show, claim, submit, approve, reject.

Queue layout (per-task JSON files, state = directory):
  ~/.cache/agent-continuity/queue/
    queued/{task-id}.json
    claimed/{task-id}.json
    completed/{task-id}.json
    awaiting-approval/{task-id}.json
    rejected/{task-id}.json
    failed/{task-id}.json
    cancelled/{task-id}.json

State transitions are atomic where they need to be:
  - claim: os.rename(queued/T.json → claimed/T.json.claiming-{pid}-{rand}) is
    the exclusion primitive. POSIX guarantees rename atomicity — exactly one
    rename succeeds when src exists; the loser sees FileNotFoundError.
    Winner exclusively owns the marker, modifies it in place, then renames
    marker → final.
  - enqueue / submit / approve / reject: write to destination via tmp +
    os.replace, then unlink src (where there was one).

Audit trail lives inside task.audit.transitions per the worker-task schema.

Approval semantics: awaiting-approval can be entered two ways:
  1. At enqueue, if policy's require_human_approval_for matches the task's
     kind or trust_level — pre-claim, no result yet.
  2. At submit, if the worker sets --needs-approval OR if expected_artifacts
     declared on the task aren't fulfilled by result.artifacts — post-submit,
     result populated.
Approve routes a pre-claim task back to queued (so a worker can pick it up)
and a post-submit task to completed. Reject sends pre-claim to cancelled and
post-submit to rejected.
"""

from __future__ import annotations
import argparse
import copy
import fnmatch
import json
import os
import secrets
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# M8.3 worker-result decision writeback. Importing _decisions here couples
# _worker → _decisions, which is acceptable: M8.3 is the integration point
# where the worker subsystem depends on the decision-log subsystem. Doctor
# stays free of _worker/_decisions imports (M7.2 / M8.1 pattern) so health
# checks survive even if one subsystem is broken.
from _decisions import append_entries_from_worker, WorkerDecisionDraftError

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
WORKER_VERSION = "1.0"

# M11.0: honor XDG base dirs for both the queue (cache-class) and the
# trust policy (config-class) so the always-sandboxed quickstart can
# fully isolate. Matches the existing M8.0 decisions / M10.0 device-
# identity / sync-metadata path conventions.
_XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache"))
_XDG_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
QUEUE_ROOT = _XDG_CACHE_HOME / "agent-continuity" / "queue"
TRUST_POLICY = _XDG_CONFIG_HOME / "agent-continuity" / "trust-policy.json"

STATES = ("queued", "claimed", "running", "completed", "awaiting-approval",
          "rejected", "failed", "cancelled")
LEVELS = ["read-only", "scoped-write", "repo-write", "elevated"]
KINDS = ["code-change", "code-review", "debug", "research", "explain",
         "test-run", "artifact-generation", "data-extraction", "other"]
WORKERS = ["claude", "codex", "chatgpt", "gemini", "grok", "kimi"]

# .mjs grant vocabulary (preserved verbatim from agent-worker.mjs).
MJS_MODES = ["review", "debug", "implement", "research", "explain"]
MJS_FILESYSTEMS = ["read_only", "workspace_write"]

# Translations used by _grant_matches_task to bridge this layer's task vocabulary
# (kind, trust_level) with .mjs's grant vocabulary (mode, filesystem). Kinds /
# levels not in these maps mean "no .mjs equivalent" → never auto-approvable
# by a .mjs-style grant (operator must use this layer's approve flow).
KIND_TO_MODE = {
    "code-change": "implement",
    "code-review": "review",
    "debug": "debug",
    "research": "research",
    "explain": "explain",
    # test-run / artifact-generation / data-extraction / other have no .mjs mode
}
LEVEL_TO_FILESYSTEM = {
    "read-only": "read_only",
    "scoped-write": "workspace_write",
    # repo-write / elevated never auto-approved by a grant (operator-only territory)
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_task_id() -> str:
    return "task-" + secrets.token_hex(6)


def _new_grant_id() -> str:
    """Mirrors agent-worker.mjs grant_id format: grant_<14-digit-utc-timestamp>_<6-hex>
    so grants migrated from .mjs keep their IDs interpretable as the same format."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"grant_{ts}_{secrets.token_hex(3)}"


# Bootstrap default used when trust-add runs on a machine with no policy file
# yet. "No default authority" is encoded as a valid denying RepoPolicy, NOT
# null — null would violate trust-policy.schema.json (default is required +
# non-nullable RepoPolicy). The denying shape:
#   - max_trust_level: read-only — no writes possible under default
#   - allow_kinds: [] — every kind is refused at enqueue
#   - require_human_approval_for: code-change / repo-write / elevated —
#     belt-and-braces for the cases where allow_kinds is later relaxed
#   - allowed_workers: claude + codex only — keeps the historical local
#     worker defaults. Web model adapters require explicit repo grants
#   - files_denied: common secret globs so even a relaxed allow_kinds can't
#     touch .env / *secret* / *credential* without explicit override
# Operators MUST add a repo grant (or relax the default) before any task can
# flow. This is the safe-by-default posture per operator's preference for
# "denying policy, not null".
_BOOTSTRAP_DEFAULT_POLICY: dict[str, Any] = {
    "max_trust_level": "read-only",
    "allow_kinds": [],
    "deny_kinds": [],
    "require_human_approval_for": ["code-change", "repo-write", "elevated"],
    "allowed_workers": ["claude", "codex"],
    "files_denied": ["**/.env*", "**/*secret*", "**/*credential*"],
}


def _load_policy_or_default() -> dict[str, Any]:
    """Like _load_policy but returns a valid conservative-denying policy
    structure if the file doesn't exist. Used by trust-* commands so trust-add
    can bootstrap a fresh policy file without requiring a separate init step.

    The bootstrap default rejects every enqueue (allow_kinds is empty) and
    requires explicit human approval for code-change / repo-write / elevated.
    The operator must add a repo grant or relax the default before any task
    can flow.

    deepcopy isolates the template from caller mutations — appending to
    grants[] on the returned dict must not pollute future bootstraps."""
    p = _load_policy()
    if p is not None:
        return p
    return {
        "schema_version": "1.0",
        "default": copy.deepcopy(_BOOTSTRAP_DEFAULT_POLICY),
        "repos": [],
        "grants": [],
    }


def _atomic_write_policy(policy: dict[str, Any]) -> tuple[bool, str | None]:
    """Atomic write: tmp + os.replace into TRUST_POLICY path. Backs up the
    existing policy alongside (`.bak-<timestamp>`) so trust-* mutations are
    recoverable. Returns (success, error)."""
    try:
        TRUST_POLICY.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except (PermissionError, OSError) as e:
        return False, f"could not create {TRUST_POLICY.parent}: {e}"
    # Backup existing
    if TRUST_POLICY.exists():
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        bak = TRUST_POLICY.with_name(TRUST_POLICY.name + f".bak-{ts}")
        try:
            bak.write_bytes(TRUST_POLICY.read_bytes())
        except (PermissionError, OSError) as e:
            return False, f"could not back up existing policy: {e}"
    tmp = TRUST_POLICY.with_name(TRUST_POLICY.name + f".tmp-{os.getpid()}")
    try:
        tmp.write_text(json.dumps(policy, indent=2) + "\n")
        os.replace(tmp, TRUST_POLICY)
        os.chmod(TRUST_POLICY, 0o600)
        return True, None
    except (PermissionError, OSError) as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False, f"could not write trust policy: {e}"


def _grant_matches_task(grant: dict[str, Any], task: dict[str, Any]) -> tuple[bool, str]:
    """Mirrors agent-worker.mjs grantMatchesTask + trustCheck — 9 AND conditions.
    Returns (matches, human_reason). The reason is meaningful in both branches:
    on match it explains what hit; on no-match it explains the first violation."""
    # 1. Worker match
    task_worker = (task.get("target") or {}).get("adapter")
    if grant.get("worker") != task_worker:
        return False, f"worker mismatch: grant={grant.get('worker')!r}, task={task_worker!r}"

    # 2. Repo match (exact or recursive subtree)
    grant_repo = grant.get("repo")
    task_repo = (task.get("input") or {}).get("repo")
    if not grant_repo or not task_repo:
        return False, "task or grant missing repo"
    if grant.get("recursive"):
        if not (task_repo == grant_repo or task_repo.startswith(grant_repo.rstrip("/") + "/")):
            return False, f"repo not under recursive grant: task={task_repo!r}, grant={grant_repo!r}"
    else:
        if task_repo != grant_repo:
            return False, f"repo mismatch (non-recursive): task={task_repo!r}, grant={grant_repo!r}"

    # 3. Mode match (translate task.kind → .mjs mode)
    task_mode = KIND_TO_MODE.get(task.get("kind") or "")
    if not task_mode:
        return False, f"task.kind {task.get('kind')!r} has no .mjs mode equivalent (not grantable)"
    if task_mode not in (grant.get("modes") or []):
        return False, f"mode {task_mode!r} not in grant.modes {grant.get('modes')}"

    # 4. Filesystem match (prefer explicit task.permissions.filesystem, else
    #    translate task.trust_level)
    perms = task.get("permissions") or {}
    task_fs = perms.get("filesystem") or LEVEL_TO_FILESYSTEM.get(task.get("trust_level") or "")
    if not task_fs:
        return False, (
            f"no filesystem mode for task: permissions.filesystem missing and "
            f"trust_level {task.get('trust_level')!r} doesn't map (repo-write / elevated "
            f"are never grantable)"
        )
    if task_fs != grant.get("filesystem"):
        return False, f"filesystem mismatch: task={task_fs!r}, grant={grant.get('filesystem')!r}"

    # 5. can_run_tests — if task wants tests, grant must permit
    if perms.get("can_run_tests") and not grant.get("can_run_tests"):
        return False, "task requests can_run_tests=true but grant doesn't permit"

    # 6. Network must be off
    if (perms.get("network") or "off") != "off":
        return False, f"task.permissions.network must be 'off', got {perms.get('network')!r}"

    # 7. dangerous_bypass must be false
    if perms.get("dangerous_bypass") is True:
        return False, "task.permissions.dangerous_bypass=true is never auto-approved"

    # 8. Timeout ≤ grant max
    task_timeout = perms.get("timeout_sec", 900)
    grant_max = grant.get("max_timeout_sec", 3600)
    if task_timeout > grant_max:
        return False, f"task timeout {task_timeout}s exceeds grant max {grant_max}s"

    # 9. Expiry / disabled
    if grant.get("disabled"):
        return False, "grant is disabled"
    expires = grant.get("expires_at")
    if expires and expires <= _now():
        return False, f"grant expired at {expires}"

    return True, f"all 9 conditions match (grant_id={grant.get('grant_id')!r})"


def _state_dir(state: str) -> Path:
    return QUEUE_ROOT / state


def _find_task(task_id: str) -> tuple[Path | None, str | None]:
    for state in STATES:
        p = _state_dir(state) / f"{task_id}.json"
        if p.exists():
            return p, state
    return None, None


def _write_task_to_state(task: dict[str, Any], state: str) -> tuple[Path | None, str | None]:
    """Atomic write: tmp + os.replace into the state dir. Returns (path, None) on
    success or (None, error_message) on PermissionError / OSError. Caller unlinks
    src separately if it had one."""
    try:
        dest_dir = _state_dir(state)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{task['id']}.json"
        tmp = dest.with_name(dest.name + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(task, indent=2))
        os.replace(tmp, dest)
        return dest, None
    except (PermissionError, OSError) as e:
        return None, f"could not write to {state}/: {e}"


def _artifact_matches(expected: dict[str, Any], produced: Any) -> bool:
    """Match an expected_artifact entry against a produced artifact.

    Produced may be:
      - dict (this layer's shape: {kind, path, ...}) → match on BOTH kind+path
      - string (.mjs / worker-result.schema.json shape: bare path)
                → match on path only; kind is implicit
      - anything else → no match (defensive)

    The dual shape is intentional: M4 tasks produced via queue_client use
    object artifacts; .mjs results validated against worker-result.schema.json
    use string artifacts. cmd_submit normalizes both transparently."""
    if isinstance(produced, str):
        return produced == expected.get("path")
    if isinstance(produced, dict):
        return (
            produced.get("kind") == expected.get("kind")
            and produced.get("path") == expected.get("path")
        )
    return False


def _audit(task: dict[str, Any], from_state: str | None, to_state: str, by: str,
           extra: dict[str, Any] | None = None) -> None:
    entry = {
        "at": _now(),
        "from": from_state or "",
        "to": to_state,
        "by": by,
    }
    if extra:
        entry.update(extra)
    task.setdefault("audit", {}).setdefault("transitions", []).append(entry)


def _load_policy() -> dict[str, Any] | None:
    if not TRUST_POLICY.exists():
        return None
    try:
        return json.loads(TRUST_POLICY.read_text())
    except Exception:
        return None


def _effective_policy(policy: dict[str, Any], repo: str | None) -> dict[str, Any] | None:
    """Per-repo policy if matched, else policy.default."""
    if repo:
        for entry in policy.get("repos", []):
            if entry.get("origin") == repo:
                return entry.get("policy")
    return policy.get("default")


def _check_policy(task: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Resolve a task against the trust policy.

    Returns:
      {
        "allow": bool,
        "effective_status": "queued" | "awaiting-approval" | "rejected",
        "reasons": [...],   # one per rule outcome
        "matched_repo": str | None,
      }
    """
    out: dict[str, Any] = {
        "allow": True,
        "effective_status": "queued",
        "reasons": [],
        "matched_repo": None,
    }
    repo = task.get("input", {}).get("repo")
    repo_policy = _effective_policy(policy, repo)
    if not repo_policy:
        out["allow"] = False
        out["effective_status"] = "rejected"
        out["reasons"].append("no policy matches and no default")
        return out
    out["matched_repo"] = repo if any(
        e.get("origin") == repo for e in policy.get("repos", [])
    ) else None

    # trust_level ≤ max
    max_level = repo_policy.get("max_trust_level", "read-only")
    if task["trust_level"] not in LEVELS:
        out["allow"] = False
        out["effective_status"] = "rejected"
        out["reasons"].append(f"unknown trust_level: {task['trust_level']}")
    elif LEVELS.index(task["trust_level"]) > LEVELS.index(max_level):
        out["allow"] = False
        out["effective_status"] = "rejected"
        out["reasons"].append(
            f"trust_level '{task['trust_level']}' exceeds repo max '{max_level}'"
        )

    kind = task["kind"]
    if kind not in repo_policy.get("allow_kinds", []):
        out["allow"] = False
        out["effective_status"] = "rejected"
        out["reasons"].append(f"kind '{kind}' not in allow_kinds")
    if kind in repo_policy.get("deny_kinds", []):
        out["allow"] = False
        out["effective_status"] = "rejected"
        out["reasons"].append(f"kind '{kind}' is in deny_kinds")

    target = task.get("target", {}).get("adapter")
    if target not in repo_policy.get("allowed_workers", []):
        out["allow"] = False
        out["effective_status"] = "rejected"
        out["reasons"].append(f"target adapter '{target}' not in allowed_workers")

    exp = repo_policy.get("expires_at")
    if exp and exp < _now():
        out["allow"] = False
        out["effective_status"] = "rejected"
        out["reasons"].append(f"policy expired at {exp}")

    # files_denied — refuse if any input.files_allowed glob matches a denied glob.
    # Conservative: if files_allowed isn't specified, we don't pre-check; the worker
    # is responsible for not writing outside the (effectively empty) allow set.
    files_allowed = task.get("input", {}).get("files_allowed", [])
    files_denied = repo_policy.get("files_denied", [])
    for fa in files_allowed:
        for fd in files_denied:
            if fnmatch.fnmatch(fa, fd) or fa == fd:
                out["allow"] = False
                out["effective_status"] = "rejected"
                out["reasons"].append(
                    f"files_allowed glob '{fa}' overlaps files_denied '{fd}'"
                )

    if not out["allow"]:
        return out

    approval = repo_policy.get("require_human_approval_for", [])
    if kind in approval or task["trust_level"] in approval:
        out["effective_status"] = "awaiting-approval"
        out["reasons"].append(
            f"kind '{kind}' or trust_level '{task['trust_level']}' requires human approval"
        )

    return out


# ── subcommands ────────────────────────────────────────────────────────────

def cmd_enqueue(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    # M14.0: auto-register the project for cwd if it has a git remote.
    # The task already carries project_uuid (from --project); this just
    # makes sure that the project we're enqueueing FROM exists in the
    # local registry, so a fresh agent later can resolve it. No-op for
    # non-git cwds.
    try:
        from _project import ensure_project_registered
        ensure_project_registered()
    except Exception:  # noqa: BLE001 — must not block enqueue
        pass

    policy = _load_policy()
    if policy is None:
        return 1, {"error": f"no trust policy at {TRUST_POLICY} — run doctor"}

    if args.instruction == "-":
        instruction = sys.stdin.read()
    else:
        ip = Path(args.instruction)
        if not ip.exists():
            return 1, {"error": f"instruction file not found: {ip}"}
        instruction = ip.read_text()

    task: dict[str, Any] = {
        "schema_version": "1.0",
        "id": _new_task_id(),
        "project_uuid": args.project,
        "source": {"adapter": args.source_adapter, "actor": args.source_actor},
        "target": {"adapter": args.target},
        "kind": args.kind,
        "trust_level": args.trust_level,
        "status": "queued",
        "created_at": _now(),
        "input": {"instruction": instruction.strip()},
    }
    if args.repo:
        task["input"]["repo"] = args.repo
    if args.branch:
        task["input"]["branch"] = args.branch
    if args.files_allowed:
        task["input"]["files_allowed"] = args.files_allowed
    if args.expected_artifact:
        parsed_artifacts = []
        for kp in args.expected_artifact:
            if ":" not in kp:
                return 1, {
                    "error": (
                        f"malformed --expected-artifact value {kp!r}: "
                        f"expected 'kind:path' (e.g. 'report:fix.diff', 'patch:src/foo.diff')"
                    ),
                }
            kind, _, path = kp.partition(":")
            if not kind or not path:
                return 1, {
                    "error": (
                        f"malformed --expected-artifact value {kp!r}: "
                        f"both kind and path must be non-empty"
                    ),
                }
            parsed_artifacts.append({"kind": kind, "path": path})
        task["expected_artifacts"] = parsed_artifacts

    # Per-task permissions block (M5.1.1). Preserved verbatim onto the task
    # for round-trip with .mjs, with two defensive refusals at enqueue:
    #   - dangerous_bypass=true is never authorized by this layer
    #   - network must be 'off' (or absent); this layer never authorizes
    #     worker network access. .mjs already enforces this at execution
    #     time; we refuse at enqueue too so a misconfigured caller can't
    #     even land such a task in the queue.
    if args.permissions:
        try:
            perms = json.loads(args.permissions)
        except json.JSONDecodeError as e:
            return 1, {"error": f"malformed --permissions JSON: {e}"}
        if not isinstance(perms, dict):
            return 1, {"error": "--permissions must be a JSON object"}
        if perms.get("dangerous_bypass") is True:
            return 2, {
                "error": (
                    "permissions.dangerous_bypass=true is refused at enqueue. "
                    "This layer never authorizes dangerous bypass."
                ),
            }
        network = perms.get("network")
        if network is not None and network != "off":
            return 2, {
                "error": (
                    f"permissions.network={network!r} is refused at enqueue. "
                    f"Only 'off' is allowed; this layer never authorizes worker network access."
                ),
            }
        task["permissions"] = perms

    check = _check_policy(task, policy)
    task["status"] = check["effective_status"]
    _audit(task, None, task["status"],
           f"enqueue:{args.source_actor}",
           extra={"policy_reasons": check["reasons"]})

    dest, err = _write_task_to_state(task, task["status"])
    if err:
        return 1, {"error": err, "task_id": task["id"]}
    return (0 if check["effective_status"] != "rejected" else 2), {
        "task_id": task["id"],
        "status": task["status"],
        "matched_repo": check["matched_repo"],
        "reasons": check["reasons"],
        "path": str(dest),
    }


def cmd_list(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    states = [args.state] if args.state else list(STATES)
    out: dict[str, Any] = {"queue_root": str(QUEUE_ROOT), "by_state": {}}
    for state in states:
        d = _state_dir(state)
        files = sorted(d.glob("task-*.json")) if d.exists() else []
        out["by_state"][state] = []
        for f in files:
            try:
                task = json.loads(f.read_text())
                out["by_state"][state].append({
                    "id": task.get("id", f.stem),
                    "kind": task.get("kind"),
                    "trust_level": task.get("trust_level"),
                    "target": task.get("target", {}).get("adapter"),
                    "created_at": task.get("created_at"),
                    "claimed_by": task.get("claimed_by"),
                    "path": str(f),
                })
            except Exception as e:
                out["by_state"][state].append({"id": f.stem, "error": str(e), "path": str(f)})
    out["total"] = sum(len(v) for v in out["by_state"].values())
    return 0, out


def cmd_show(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    p, state = _find_task(args.task_id)
    if not p:
        return 1, {"error": f"task not found: {args.task_id}"}
    return 0, {"task": json.loads(p.read_text()), "state": state, "path": str(p)}


def cmd_claim(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Claim a queued task.

    Race safety: uses os.rename(src → unique marker) as the exclusion primitive.
    POSIX guarantees rename is atomic; if two workers race, only one rename
    succeeds — the other gets FileNotFoundError because src no longer exists.

    Ownership: refuses to claim if task.target.adapter doesn't match --adapter
    (e.g. a codex worker cannot take a chatgpt-targeted task).

    Policy: re-verifies trust policy at claim time. If the policy tightened
    between enqueue and claim, the task transitions to rejected with audit
    trail rather than being claimed.
    """
    src = _state_dir("queued") / f"{args.task_id}.json"
    if not src.exists():
        return 1, {"error": f"task {args.task_id} not in queued state"}

    # ATOMIC OWNERSHIP: rename src to a unique marker. First rename wins; loser
    # gets FileNotFoundError because src is already gone.
    claimed_dir = _state_dir("claimed")
    try:
        claimed_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        return 1, {"error": f"could not create claimed/ dir: {e}"}
    marker = claimed_dir / f"{args.task_id}.json.claiming-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        os.rename(src, marker)
    except FileNotFoundError:
        return 2, {
            "task_id": args.task_id,
            "error": "task is no longer queued (claimed by another worker, or never existed)",
        }
    except (PermissionError, OSError) as e:
        return 1, {"error": f"could not acquire claim marker: {e}"}

    # We exclusively own `marker`. From here on no other process can interfere.
    def _restore_to_queued(reason_for_log: str) -> None:
        """Best-effort: put the task back in queued/ so it can be picked up later."""
        try:
            os.rename(marker, src)
        except Exception:
            pass  # leave marker; operator will clean up

    try:
        task = json.loads(marker.read_text())
    except Exception as e:
        _restore_to_queued("unreadable task file")
        return 1, {"error": f"could not read task: {e}"}

    # Adapter ownership: this worker's adapter must match task.target.adapter.
    task_target_adapter = task.get("target", {}).get("adapter")
    if task_target_adapter != args.adapter:
        _restore_to_queued("adapter mismatch")
        return 1, {
            "task_id": args.task_id,
            "error": (
                f"task targets adapter '{task_target_adapter}', "
                f"this worker reports adapter '{args.adapter}'. "
                f"Task restored to queued/ for the correct worker to claim."
            ),
            "task_target_adapter": task_target_adapter,
            "worker_adapter": args.adapter,
        }

    # P3 hardening (M5.3a): permissions recheck at claim. Catches tasks that
    # bypassed enqueue's refusal by being manually inserted into queued/
    # (cp / mv from another machine, malicious operator, broken migration).
    perm_err = _refuse_dangerous_permissions(task)
    if perm_err:
        _restore_to_queued("permissions recheck failed")
        return 2, {"task_id": args.task_id, "error": perm_err}

    # Re-verify policy at claim time (defense in depth — policy may have tightened).
    policy = _load_policy()
    if policy is None:
        _restore_to_queued("no policy")
        return 1, {"error": f"no trust policy at {TRUST_POLICY}"}
    check = _check_policy(task, policy)
    if not check["allow"]:
        task["status"] = "rejected"
        _audit(task, "queued", "rejected",
               f"claim-policy:{args.worker}",
               extra={"policy_reasons": check["reasons"]})
        # Write to rejected/, remove marker.
        rejected_path, err = _write_task_to_state(task, "rejected")
        if err:
            _restore_to_queued("write failed")
            return 1, {"error": err, "task_id": args.task_id}
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
        return 2, {
            "task_id": args.task_id,
            "status": "rejected",
            "reasons": check["reasons"],
            "path": str(rejected_path),
        }

    # All checks pass. Update task fields, write to marker in-place, then atomic
    # rename to final claimed path. The rename is from a file we exclusively own
    # so there is no contention point.
    task["status"] = "claimed"
    task["claimed_at"] = _now()
    task["claimed_by"] = args.worker
    task["claimed_by_adapter"] = args.adapter
    _audit(task, "queued", "claimed", args.worker)

    final = claimed_dir / f"{args.task_id}.json"
    try:
        marker.write_text(json.dumps(task, indent=2))
        os.replace(marker, final)
    except (PermissionError, OSError) as e:
        # Marker is left behind; operator can clean. Task is NOT in queued/.
        return 1, {"error": f"could not finalize claim: {e}"}

    return 0, {
        "task_id": args.task_id,
        "status": "claimed",
        "claimed_by": args.worker,
        "claimed_by_adapter": args.adapter,
        "path": str(final),
    }


def _refuse_dangerous_permissions(task: dict[str, Any]) -> str | None:
    """Defense-in-depth recheck of permissions.dangerous_bypass / network.
    Returns None if safe to proceed, else an error string. Called at claim and
    start to catch tasks manually inserted into queued/ that have malicious
    permissions (the enqueue-time refusal only fires when worker.sh enqueue
    actually constructs the task; nothing stops an operator from `cp`ing a
    crafted task JSON into queued/ directly)."""
    perms = task.get("permissions") or {}
    if perms.get("dangerous_bypass") is True:
        return (
            "task.permissions.dangerous_bypass=true is refused. "
            "This layer never authorizes dangerous bypass."
        )
    network = perms.get("network")
    if network is not None and network != "off":
        return (
            f"task.permissions.network={network!r} is refused. "
            f"Only 'off' is allowed; this layer never authorizes worker network access."
        )
    return None


def cmd_start(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Transition a claimed task → running. M5.3a addition.

    Mirrors the M4.1 worker-claim atomicity pattern: os.rename as exclusion
    primitive so two callers racing on the same claimed task can't both win.

    Ownership: refuses unless args.worker == task.claimed_by (only the worker
    that claimed can start). Adapter cross-check: refuses unless
    args.adapter == task.claimed_by_adapter == task.target.adapter.

    Permissions recheck (M5.3a defense in depth): re-verifies
    permissions.dangerous_bypass / network at this transition, catching tasks
    whose permissions were mutated between claim and start (or whose
    permissions slipped past enqueue because the task was hand-inserted into
    the queue)."""
    src = _state_dir("claimed") / f"{args.task_id}.json"
    if not src.exists():
        return 1, {
            "task_id": args.task_id,
            "error": f"task {args.task_id} not in claimed state",
        }

    # ATOMIC OWNERSHIP: same primitive as cmd_claim. First rename wins.
    running_dir = _state_dir("running")
    try:
        running_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        return 1, {"error": f"could not create running/ dir: {e}"}
    marker = running_dir / f"{args.task_id}.json.starting-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        os.rename(src, marker)
    except FileNotFoundError:
        return 2, {
            "task_id": args.task_id,
            "error": "task no longer in claimed state (another caller started it, or task moved)",
        }
    except (PermissionError, OSError) as e:
        return 1, {"error": f"could not acquire start marker: {e}"}

    def _restore_to_claimed() -> None:
        try:
            os.rename(marker, src)
        except Exception:
            pass

    try:
        task = json.loads(marker.read_text())
    except Exception as e:
        _restore_to_claimed()
        return 1, {"error": f"could not read task: {e}"}

    # Ownership: only the worker that claimed can start.
    if task.get("claimed_by") != args.worker:
        _restore_to_claimed()
        return 1, {
            "task_id": args.task_id,
            "error": (
                f"task was claimed by '{task.get('claimed_by')}', "
                f"start attempted by '{args.worker}'. Refusing."
            ),
            "claimed_by": task.get("claimed_by"),
        }

    # Adapter consistency: claim should have set claimed_by_adapter; double-check.
    expected_adapter = task.get("claimed_by_adapter") or (task.get("target") or {}).get("adapter")
    if args.adapter != expected_adapter:
        _restore_to_claimed()
        return 1, {
            "task_id": args.task_id,
            "error": (
                f"adapter mismatch: task claimed_by_adapter={expected_adapter!r}, "
                f"start args.adapter={args.adapter!r}"
            ),
        }

    # P3 hardening: permissions recheck at start.
    perm_err = _refuse_dangerous_permissions(task)
    if perm_err:
        _restore_to_claimed()
        return 2, {"task_id": args.task_id, "error": perm_err}

    # All checks pass. Update task and atomic-rename to final.
    task["status"] = "running"
    task["started_at"] = _now()
    _audit(task, "claimed", "running", args.worker)

    final = running_dir / f"{args.task_id}.json"
    try:
        marker.write_text(json.dumps(task, indent=2))
        os.replace(marker, final)
    except (PermissionError, OSError) as e:
        return 1, {"error": f"could not finalize start: {e}"}

    return 0, {
        "task_id": args.task_id,
        "status": "running",
        "started_by": args.worker,
        "started_by_adapter": args.adapter,
        "started_at": task["started_at"],
        "path": str(final),
    }


def cmd_submit(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Submit a claimed task's result.

    Ownership: refuses if args.worker doesn't match task.claimed_by. The worker
    that took the task is the only one allowed to submit it. Mismatch usually
    means a race or a confused caller; either way refuse rather than let a
    different worker overwrite results."""
    p, state = _find_task(args.task_id)
    if not p:
        return 1, {"error": f"task not found: {args.task_id}"}
    if state not in ("claimed", "running"):
        return 1, {
            "error": (
                f"task is in state '{state}', cannot submit "
                f"(must be in 'claimed' or 'running')"
            ),
        }

    task = json.loads(p.read_text())

    # Ownership: only the worker that claimed can submit.
    if task.get("claimed_by") != args.worker:
        return 1, {
            "task_id": args.task_id,
            "error": (
                f"task was claimed by '{task.get('claimed_by')}', "
                f"submit attempted by '{args.worker}'. Refusing to overwrite."
            ),
            "claimed_by": task.get("claimed_by"),
        }

    if args.result == "-":
        result_text = sys.stdin.read()
    else:
        rp = Path(args.result)
        if not rp.exists():
            return 1, {"error": f"result file not found: {rp}"}
        result_text = rp.read_text()

    try:
        result = json.loads(result_text)
        if not isinstance(result, dict):
            # Valid JSON but not an object (list/string/number/bool/null). Wrap
            # so downstream result.get(...) calls don't crash. Original is
            # preserved verbatim in the summary field.
            result = {"summary": json.dumps(result)}
    except json.JSONDecodeError:
        result = {"summary": result_text.strip()}

    # Expected-artifacts completeness check: for each entry in
    # task.expected_artifacts, look for a result.artifacts entry with the same
    # (kind, path). Missing entries soft-route the task to awaiting-approval so
    # a human can decide whether the gap is acceptable. Worker can also
    # explicitly set --needs-approval (which we honor below regardless).
    expected = task.get("expected_artifacts", [])
    produced = result.get("artifacts", []) if isinstance(result.get("artifacts"), list) else []
    # _artifact_matches accepts both {kind,path} dicts (this layer's shape)
    # and bare-path strings (.mjs / worker-result.schema.json shape) so a
    # native .mjs result with artifacts: ["fixtures/sample.md.diff"] doesn't
    # crash on .get() calls. String artifacts match expected.path only.
    missing = [
        e for e in expected
        if not any(_artifact_matches(e, p) for p in produced)
    ]

    # M5.3c: optional subprocess metadata attached to task.process for debug
    # completeness. Validated as a JSON object; refused if not parseable or
    # not a dict. Surface for runners (e.g. .mjs bridge) to record exit code,
    # signal, timeout flag, duration, and log path on the canonical task.
    if args.process_info:
        try:
            proc_info = json.loads(args.process_info)
        except json.JSONDecodeError as e:
            return 1, {"error": f"malformed --process-info JSON: {e}"}
        if not isinstance(proc_info, dict):
            return 1, {"error": "--process-info must be a JSON object"}
        task["process"] = proc_info

    if args.needs_approval:
        result["needs_human_approval"] = True
        if args.approval_reason:
            result["approval_reason"] = args.approval_reason
        next_state = "awaiting-approval"
    elif args.fail:
        next_state = "failed"
    elif missing:
        # Auto-soft-route: missing artifacts isn't a hard fail (worker may have
        # had a good reason to skip), but it shouldn't silently complete either.
        result["needs_human_approval"] = True
        result["missing_expected_artifacts"] = missing
        missing_summary = ", ".join(
            f"{m.get('kind','?')}:{m.get('path','?')}" for m in missing
        )
        existing = result.get("approval_reason", "")
        result["approval_reason"] = (
            (existing + "; " if existing else "")
            + f"missing expected artifacts: {missing_summary}"
        )
        next_state = "awaiting-approval"
    else:
        next_state = "completed"

    # M8.3: worker-submitted decisions[]. Validate the whole batch first,
    # then write to the decision log BEFORE the task state transition. Web model
    # adapters added after M9 use the same path: task.claimed_by_adapter is
    # the only authoritative attribution token. If any draft is invalid,
    # the entire submit is rejected and the task
    # remains in its current state (per M8.3 sign-off: "no partial task
    # completion with broken decision provenance"). Write-order rationale:
    # decisions-first means an OS failure mid-log-write leaves the task
    # claimed and the operator sees a clear error; the alternative would
    # complete the task and silently lose the worker's reasoning.
    #
    # M8.3.1: reject present-but-non-array `decisions`. Earlier code used
    # `if isinstance(..., list) else None`, which silently coerced
    # decisions: "bad" / decisions: {...} into "no decisions" and let the
    # submit complete. That violated the "invalid decision rejects entire
    # submit" contract and could silently lose load-bearing reasoning.
    # null and absent stay equivalent to "no decisions".
    raw_decisions = result.get("decisions")
    if raw_decisions is not None and not isinstance(raw_decisions, list):
        return 1, {
            "task_id": args.task_id,
            "error": (
                f"result.decisions must be an array; got "
                f"{type(raw_decisions).__name__}. Refusing submit to avoid "
                f"silently dropping worker reasoning."
            ),
        }
    draft_decisions = raw_decisions  # None or list (possibly empty)
    appended_ids: list[str] = []
    if draft_decisions:
        # adapter: the canonical adapter token lives at task.claimed_by_adapter.
        # task.claimed_by is a longer
        # descriptive identifier (e.g. 'codex-on-device.local-via-mjs') that
        # would fail the decision-entry adapter enum. M8.3 sign-off framed
        # this as 'derive from task'; the canonical task field on disk is
        # claimed_by_adapter per the actual worker-task schema.
        decision_adapter = task.get("claimed_by_adapter")
        # repo: trust the canonical task field, not cwd or worker output.
        # M8.3 sign-off framed this as 'task.repo_origin -> task.repo ->
        # unknown', but the actual worker-task schema places repo at
        # task.input.repo. Same intent: derive from the trusted task
        # record, fall back to 'unknown'. The two-step indirection here
        # also covers tasks missing the input block entirely.
        task_input = task.get("input") if isinstance(task.get("input"), dict) else {}
        decision_repo = task_input.get("repo") or "unknown"
        try:
            appended_ids = append_entries_from_worker(
                draft_decisions,
                adapter=decision_adapter,
                repo=decision_repo,
                task_id=args.task_id,
            )
        except WorkerDecisionDraftError as e:
            return 1, {
                "task_id": args.task_id,
                "error": f"decision validation failed: {e}",
            }
        except TimeoutError as e:
            return 1, {
                "task_id": args.task_id,
                "error": f"could not acquire decision log lock: {e}",
            }
        except OSError as e:
            return 1, {
                "task_id": args.task_id,
                "error": f"decision log write failed: {e}",
            }

    # Submit-handler-authoritative bookkeeping. Overwrite any value the
    # worker tried to set in the submitted result.
    result["appended_decision_ids"] = appended_ids

    task["result"] = result
    task["completed_at"] = _now()
    task["status"] = next_state
    # Use actual from-state (could be 'claimed' or 'running') — honest audit.
    _audit(task, state, next_state, args.worker)
    dest, err = _write_task_to_state(task, next_state)
    if err:
        return 1, {"error": err, "task_id": args.task_id}
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    return 0, {
        "task_id": args.task_id,
        "status": next_state,
        "path": str(dest),
        "appended_decision_ids": appended_ids,
    }


def _approval_next_state(task: dict[str, Any], approve: bool) -> str:
    """Compute the next state when approving or rejecting an awaiting-approval
    task. Distinguishes pre-claim (no result yet) from post-submit (result
    populated). Approve sends pre-claim back to queued so a worker can pick it
    up; post-submit goes to completed. Reject sends pre-claim to cancelled (no
    work was done) and post-submit to rejected (work done but result refused)."""
    has_result = bool(task.get("result"))
    if approve:
        return "completed" if has_result else "queued"
    return "rejected" if has_result else "cancelled"


def _stamp_approval_resolved(task: dict[str, Any], decision: str, by: str) -> None:
    """For post-submit approve/reject (task has a result), stamp the resolution
    on the result block so a reader can tell the original needs_human_approval
    flag has been decided, without losing the historical signal of WHY approval
    was needed in the first place. Pre-claim case has no result, so this is
    a no-op for that path."""
    if not task.get("result"):
        return
    task["result"]["approval_resolved_at"] = _now()
    task["result"]["approval_resolved_by"] = by
    task["result"]["approval_decision"] = decision  # "approved" or "rejected"


def _acquire_decision_lock(task_id: str) -> tuple[Path | None, str | None]:
    """Atomic ownership primitive for approve/reject. Mirrors the M4.1
    worker-claim pattern: os.rename(awaiting-approval/T.json → marker) is
    POSIX-atomic. First operator wins; the loser sees FileNotFoundError
    because the source is already gone.

    Returns (marker_path, None) on success or (None, error_msg) on failure.
    Caller is responsible for restoring or renaming the marker."""
    src = _state_dir("awaiting-approval") / f"{task_id}.json"
    if not src.exists():
        return None, f"task {task_id} not in awaiting-approval state"
    marker = src.parent / f"{task_id}.json.deciding-{os.getpid()}-{secrets.token_hex(4)}"
    try:
        os.rename(src, marker)
        return marker, None
    except FileNotFoundError:
        return None, (
            f"task {task_id} no longer awaiting-approval "
            f"(another operator decided first)"
        )
    except (PermissionError, OSError) as e:
        return None, f"could not acquire decision lock: {e}"


def cmd_approve(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Approve a task in awaiting-approval. Routes per _approval_next_state.

    Race-safe: uses os.rename as exclusion primitive (see _acquire_decision_lock).
    If two operators race, exactly one wins; the loser sees a clean exit-2
    'no longer awaiting-approval' error."""
    marker, err = _acquire_decision_lock(args.task_id)
    if err:
        # FileNotFoundError race → exit 2 (state mismatch). Other errors → exit 1.
        rc = 2 if marker is None and "no longer" in err else 1
        return rc, {"task_id": args.task_id, "error": err}

    def _restore() -> None:
        try:
            os.rename(marker, _state_dir("awaiting-approval") / f"{args.task_id}.json")
        except Exception:
            pass  # leave marker; operator will clean up

    try:
        task = json.loads(marker.read_text())
    except Exception as e:
        _restore()
        return 1, {"error": f"could not read task: {e}"}

    next_state = _approval_next_state(task, approve=True)
    task["status"] = next_state
    task.setdefault("approval", {})["approved_by"] = args.by
    task["approval"]["approved_at"] = _now()
    if args.note:
        task["approval"]["note"] = args.note
    _stamp_approval_resolved(task, "approved", args.by)
    _audit(task, "awaiting-approval", next_state, f"approve:{args.by}",
           extra={"note": args.note} if args.note else None)

    # Cross-dir atomic rename: marker (in awaiting-approval/) → final (in next_state/).
    final_dir = _state_dir(next_state)
    try:
        final_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        return 1, {"error": f"could not create {final_dir}: {e}"}
    final = final_dir / f"{args.task_id}.json"
    try:
        marker.write_text(json.dumps(task, indent=2))
        os.replace(marker, final)
    except (PermissionError, OSError) as e:
        # Marker left in awaiting-approval/; operator clean up. Task is NOT
        # in awaiting-approval/ as a regular file (we own the marker).
        return 1, {"error": f"could not finalize approval: {e}"}

    return 0, {
        "task_id": args.task_id,
        "status": next_state,
        "approved_by": args.by,
        "path": str(final),
    }


def cmd_reject(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Reject a task in awaiting-approval. Routes per _approval_next_state.

    Race-safe: same rename-as-lock primitive as cmd_approve. If one operator
    approves while another rejects, exactly one wins."""
    marker, err = _acquire_decision_lock(args.task_id)
    if err:
        rc = 2 if marker is None and "no longer" in err else 1
        return rc, {"task_id": args.task_id, "error": err}

    def _restore() -> None:
        try:
            os.rename(marker, _state_dir("awaiting-approval") / f"{args.task_id}.json")
        except Exception:
            pass

    try:
        task = json.loads(marker.read_text())
    except Exception as e:
        _restore()
        return 1, {"error": f"could not read task: {e}"}

    next_state = _approval_next_state(task, approve=False)
    task["status"] = next_state
    task.setdefault("approval", {})["rejected_by"] = args.by
    task["approval"]["rejected_at"] = _now()
    if args.reason:
        task["approval"]["reason"] = args.reason
    _stamp_approval_resolved(task, "rejected", args.by)
    _audit(task, "awaiting-approval", next_state, f"reject:{args.by}",
           extra={"reason": args.reason} if args.reason else None)

    final_dir = _state_dir(next_state)
    try:
        final_dir.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError) as e:
        return 1, {"error": f"could not create {final_dir}: {e}"}
    final = final_dir / f"{args.task_id}.json"
    try:
        marker.write_text(json.dumps(task, indent=2))
        os.replace(marker, final)
    except (PermissionError, OSError) as e:
        return 1, {"error": f"could not finalize rejection: {e}"}

    return 0, {
        "task_id": args.task_id,
        "status": next_state,
        "rejected_by": args.by,
        "reason": args.reason,
        "path": str(final),
    }


# ── trust-* subcommands (M5.2a, .mjs-style grants) ────────────────────────

def cmd_trust_list(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Dump the trust policy. Mirrors .mjs's trust-list."""
    policy = _load_policy()
    if policy is None:
        return 0, {
            "policy_path": str(TRUST_POLICY),
            "present": False,
            "message": f"no trust policy at {TRUST_POLICY} (no grants, no default, no repos)",
            "grants": [],
            "repos": [],
            "default": None,
        }
    return 0, {
        "policy_path": str(TRUST_POLICY),
        "present": True,
        "schema_version": policy.get("schema_version"),
        "default": policy.get("default"),
        "repos": policy.get("repos", []),
        "grants": policy.get("grants", []),
    }


def cmd_trust_add(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Append a .mjs-style grant to policy.grants[]. Mirrors .mjs's trust-add."""
    # Validate inputs
    if args.worker not in WORKERS:
        return 1, {"error": f"--worker must be one of {WORKERS}"}
    if args.filesystem not in MJS_FILESYSTEMS:
        return 1, {"error": f"--filesystem must be one of {MJS_FILESYSTEMS}"}
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    if not modes:
        return 1, {"error": "--modes must be a non-empty comma-separated list"}
    bad_modes = [m for m in modes if m not in MJS_MODES]
    if bad_modes:
        return 1, {"error": f"unknown mode(s) {bad_modes!r}; must be from {MJS_MODES}"}
    if args.dangerous_bypass:
        return 2, {
            "error": (
                "--dangerous-bypass (grant.dangerous_bypass=true) is never accepted. "
                "This layer refuses it at grant creation, mirroring .mjs's refusal at "
                "execution time."
            ),
        }

    now = _now()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=args.expires_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    grant: dict[str, Any] = {
        "grant_id": _new_grant_id(),
        "worker": args.worker,
        "repo": args.repo,
        "recursive": bool(args.recursive),
        "modes": modes,
        "filesystem": args.filesystem,
        "can_run_tests": bool(args.can_run_tests),
        "max_timeout_sec": int(args.max_timeout_sec),
        "network": "off",
        "dangerous_bypass": False,
        "created_at": now,
        "expires_at": expires_at,
        "disabled": False,
    }
    if args.note:
        grant["note"] = args.note

    policy = _load_policy_or_default()
    policy.setdefault("grants", []).append(grant)
    ok, err = _atomic_write_policy(policy)
    if not ok:
        return 1, {"error": err}
    return 0, {"grant": grant, "policy_path": str(TRUST_POLICY)}


def cmd_trust_remove(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Remove a grant by grant_id. Mirrors .mjs's trust-remove."""
    policy = _load_policy()
    if policy is None:
        return 1, {"error": f"no trust policy at {TRUST_POLICY}"}
    grants = policy.get("grants", [])
    before = len(grants)
    remaining = [g for g in grants if g.get("grant_id") != args.grant_id]
    removed = before - len(remaining)
    if removed == 0:
        return 1, {"error": f"no grant with id {args.grant_id!r}"}
    policy["grants"] = remaining
    ok, err = _atomic_write_policy(policy)
    if not ok:
        return 1, {"error": err}
    return 0, {"removed_count": removed, "grant_id": args.grant_id, "remaining_grants": len(remaining)}


def cmd_trust_check(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Check whether a task would be auto-approved by any current grant.
    Mirrors .mjs's trust-check. Read-only — does not mutate the queue or policy."""
    p, state = _find_task(args.task_id)
    if not p:
        return 1, {"error": f"task not found: {args.task_id}"}
    task = json.loads(p.read_text())

    policy = _load_policy()
    grants = (policy or {}).get("grants", []) if policy else []

    matched_grant: dict[str, Any] | None = None
    matched_reason = ""
    miss_reasons: list[str] = []
    for grant in grants:
        ok, reason = _grant_matches_task(grant, task)
        if ok:
            matched_grant = grant
            matched_reason = reason
            break
        else:
            miss_reasons.append(f"  - {grant.get('grant_id','?')}: {reason}")

    out: dict[str, Any] = {
        "task_id": args.task_id,
        "task_state": state,
        "policy_path": str(TRUST_POLICY),
        "trusted": matched_grant is not None,
        "grant_id": matched_grant.get("grant_id") if matched_grant else None,
        "reason": matched_reason if matched_grant else (
            "no grant matched"
            + (f":\n{chr(10).join(miss_reasons)}" if miss_reasons else " (no grants configured)")
        ),
        "checked_grants_count": len(grants),
    }
    # rc=0 if trusted; rc=2 if not (mirrors policy-rejection convention)
    return (0 if matched_grant else 2), out


# ── output rendering ───────────────────────────────────────────────────────

def _render_human(cmd: str, rc: int, report: dict[str, Any]) -> str:
    L: list[str] = []
    L.append(f"agent-continuity worker v{WORKER_VERSION} - {report.get('ran_at')}  cmd: {cmd}  rc: {rc}")
    if "error" in report:
        L.append(f"  error: {report['error']}")
        return "\n".join(L)

    if cmd == "enqueue":
        L.append(f"  task_id:  {report.get('task_id')}")
        L.append(f"  status:   {report.get('status')}")
        if report.get("matched_repo"):
            L.append(f"  policy:   matched repo grant for {report['matched_repo']}")
        else:
            L.append("  policy:   default (no explicit repo grant)")
        for r in report.get("reasons", []):
            L.append(f"    reason: {r}")
        L.append(f"  path:     {report.get('path')}")
    elif cmd == "list":
        L.append(f"  queue: {report['queue_root']}")
        L.append(f"  total: {report['total']}")
        for state, items in report["by_state"].items():
            if not items:
                continue
            L.append(f"  [{state}] ({len(items)})")
            for it in items:
                claim = f"  by={it['claimed_by']}" if it.get("claimed_by") else ""
                L.append(f"    {it['id']}  kind={it.get('kind')}  level={it.get('trust_level')}  target={it.get('target')}{claim}")
    elif cmd == "show":
        task = report["task"]
        L.append(f"  state:    {report['state']}")
        L.append(f"  id:       {task.get('id')}")
        L.append(f"  kind:     {task.get('kind')}")
        L.append(f"  level:    {task.get('trust_level')}")
        L.append(f"  target:   {task.get('target', {}).get('adapter')}")
        L.append(f"  created:  {task.get('created_at')}")
        if task.get("claimed_at"):
            L.append(f"  claimed:  {task['claimed_at']} by {task.get('claimed_by')}")
        if task.get("completed_at"):
            L.append(f"  done:     {task['completed_at']}")
        L.append("  transitions:")
        for t in task.get("audit", {}).get("transitions", []):
            L.append(f"    {t['at']}  {t.get('from','-'):<20s} -> {t['to']:<20s}  by {t.get('by','')}")
    elif cmd in ("claim", "submit", "start"):
        L.append(f"  task_id: {report.get('task_id')}")
        L.append(f"  status:  {report.get('status')}")
        if cmd == "start":
            L.append(f"  started_by: {report.get('started_by')} (adapter={report.get('started_by_adapter')})")
            L.append(f"  started_at: {report.get('started_at')}")
        for r in report.get("reasons", []):
            L.append(f"    reason: {r}")
        L.append(f"  path:    {report.get('path')}")
    elif cmd == "approve":
        L.append(f"  task_id:     {report.get('task_id')}")
        L.append(f"  new status:  {report.get('status')}")
        L.append(f"  approved by: {report.get('approved_by')}")
        L.append(f"  path:        {report.get('path')}")
    elif cmd == "reject":
        L.append(f"  task_id:     {report.get('task_id')}")
        L.append(f"  new status:  {report.get('status')}")
        L.append(f"  rejected by: {report.get('rejected_by')}")
        if report.get("reason"):
            L.append(f"  reason:      {report['reason']}")
        L.append(f"  path:        {report.get('path')}")
    elif cmd == "trust-list":
        L.append(f"  policy:    {report.get('policy_path')}  present={report.get('present')}")
        L.append(f"  default:   {'set' if report.get('default') else 'none'}")
        L.append(f"  repos:     {len(report.get('repos', []))}")
        L.append(f"  grants:    {len(report.get('grants', []))}")
        for g in report.get("grants", []):
            note = f"  ({g.get('note')})" if g.get("note") else ""
            L.append(f"    {g.get('grant_id')}  worker={g.get('worker')}  modes={g.get('modes')}  fs={g.get('filesystem')}  expires={g.get('expires_at')}{note}")
    elif cmd == "trust-add":
        g = report.get("grant", {})
        L.append(f"  grant_id:    {g.get('grant_id')}")
        L.append(f"  worker:      {g.get('worker')}")
        L.append(f"  repo:        {g.get('repo')}  (recursive={g.get('recursive')})")
        L.append(f"  modes:       {g.get('modes')}")
        L.append(f"  filesystem:  {g.get('filesystem')}")
        L.append(f"  expires_at:  {g.get('expires_at')}")
        L.append(f"  policy:      {report.get('policy_path')}")
    elif cmd == "trust-remove":
        L.append(f"  grant_id:    {report.get('grant_id')}")
        L.append(f"  removed:     {report.get('removed_count')}")
        L.append(f"  remaining:   {report.get('remaining_grants')}")
    elif cmd == "trust-check":
        L.append(f"  task_id:     {report.get('task_id')}  state={report.get('task_state')}")
        L.append(f"  trusted:     {report.get('trusted')}")
        L.append(f"  grant_id:    {report.get('grant_id')}")
        L.append(f"  reason:      {report.get('reason')}")
        L.append(f"  checked:     {report.get('checked_grants_count')} grant(s)")
    return "\n".join(L)


def _print(args: argparse.Namespace, rc: int, report: dict[str, Any]) -> None:
    if not args.human:
        print(json.dumps(report, indent=2, default=str))
    if not args.json:
        if not args.human:
            print("\n---\n")
        print(_render_human(args.cmd, rc, report))


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Agent-continuity worker-task queue (M4.0 minimum viable).",
    )
    parser.add_argument("--json", action="store_true", help="JSON output only")
    parser.add_argument("--human", action="store_true", help="Human output only")
    sub = parser.add_subparsers(dest="cmd", required=True)

    en = sub.add_parser("enqueue", help="Enqueue a new worker-task")
    en.add_argument("--project", required=True, help="Project UUID (proj-...)")
    en.add_argument("--kind", required=True, choices=KINDS)
    en.add_argument("--target", required=True, choices=WORKERS)
    en.add_argument("--trust-level", required=True, choices=LEVELS)
    en.add_argument("--instruction", required=True, help="Path to instruction file ('-' for stdin)")
    en.add_argument("--repo", help="Repo origin URI (governs policy lookup; required for repo-scoped grants)")
    en.add_argument("--branch", help="Git branch (for code-change tasks)")
    en.add_argument("--files-allowed", nargs="*", default=[], help="Glob(s) the worker may write to")
    en.add_argument("--expected-artifact", nargs="*", default=[],
                    help="Expected artifacts as kind:path (e.g. patch:fix.diff report:review.md)")
    en.add_argument("--permissions",
                    help="JSON object of per-task permissions (filesystem, network, can_run_tests, "
                         "dangerous_bypass, timeout_sec). Preserved verbatim onto task.permissions. "
                         "dangerous_bypass=true and network!='off' are refused at enqueue.")
    en.add_argument("--source-adapter", default="human", choices=["human", "openclaw", "claude", "codex", "chatgpt", "gemini", "grok", "kimi"])
    en.add_argument("--source-actor", default=os.environ.get("USER", "unknown"))

    li = sub.add_parser("list", help="List tasks by state")
    li.add_argument("--state", choices=STATES)

    sh = sub.add_parser("show", help="Show full task JSON + audit transitions")
    sh.add_argument("task_id")

    cl = sub.add_parser("claim", help="Claim a queued task")
    cl.add_argument("task_id")
    cl.add_argument("--adapter", required=True, choices=WORKERS,
                    help="Adapter type of this worker (must match task.target.adapter)")
    cl.add_argument("--worker", required=True, help="Worker identifier (e.g. claude-on-operator-device)")

    st = sub.add_parser("start", help="Transition a claimed task to running (M5.3a)")
    st.add_argument("task_id")
    st.add_argument("--adapter", required=True, choices=WORKERS,
                    help="Adapter type (must match task.claimed_by_adapter)")
    st.add_argument("--worker", required=True, help="Worker identifier (must match task.claimed_by)")

    su = sub.add_parser("submit", help="Submit a claimed task's result")
    su.add_argument("task_id")
    su.add_argument("--worker", required=True)
    su.add_argument("--result", required=True, help="Path to result JSON ('-' for stdin)")
    su.add_argument("--needs-approval", action="store_true")
    su.add_argument("--approval-reason")
    su.add_argument("--fail", action="store_true", help="Mark task as failed instead of completed")
    su.add_argument("--process-info",
                    help="JSON object of subprocess metadata (code, signal, timed_out, "
                         "duration_ms, log). Attached to task.process per M5.3c.")

    ap = sub.add_parser("approve", help="Approve a task in awaiting-approval")
    ap.add_argument("task_id")
    ap.add_argument("--by", required=True, help="Human approver identifier")
    ap.add_argument("--note", help="Optional approval note (recorded in audit)")

    rj = sub.add_parser("reject", help="Reject a task in awaiting-approval")
    rj.add_argument("task_id")
    rj.add_argument("--by", required=True, help="Human reviewer identifier")
    rj.add_argument("--reason", help="Why the task was rejected (recorded in audit)")

    tl = sub.add_parser("trust-list", help="Dump the trust policy (default + repos + grants)")

    ta = sub.add_parser("trust-add", help="Add a .mjs-style trust grant to the policy")
    ta.add_argument("--repo", required=True, help="Repo path the grant covers")
    ta.add_argument("--worker", required=True, choices=WORKERS)
    ta.add_argument("--modes", required=True,
                    help=f"Comma-separated modes (subset of {MJS_MODES})")
    ta.add_argument("--filesystem", required=True, choices=MJS_FILESYSTEMS)
    ta.add_argument("--expires-days", type=int, default=30, help="Days until expiry (default 30)")
    ta.add_argument("--recursive", action="store_true", help="Grant covers repo subtree")
    ta.add_argument("--can-run-tests", action="store_true")
    ta.add_argument("--max-timeout-sec", type=int, default=3600)
    ta.add_argument("--note")
    ta.add_argument("--dangerous-bypass", action="store_true",
                    help="(refused — present only to give a clear error if a caller attempts it)")

    tr = sub.add_parser("trust-remove", help="Remove a grant by grant_id")
    tr.add_argument("grant_id")

    tc = sub.add_parser("trust-check", help="Check whether a task would be auto-approved by any grant")
    tc.add_argument("task_id")

    args = parser.parse_args()
    if args.json and args.human:
        print("error: --json and --human are mutually exclusive", file=sys.stderr)
        return 64

    dispatch = {
        "enqueue": cmd_enqueue,
        "list": cmd_list,
        "show": cmd_show,
        "claim": cmd_claim,
        "start": cmd_start,
        "submit": cmd_submit,
        "approve": cmd_approve,
        "reject": cmd_reject,
        "trust-list": cmd_trust_list,
        "trust-add": cmd_trust_add,
        "trust-remove": cmd_trust_remove,
        "trust-check": cmd_trust_check,
    }
    rc, report = dispatch[args.cmd](args)
    report["cmd"] = args.cmd
    report["ran_at"] = _now()
    report["worker_version"] = WORKER_VERSION
    report["device"] = socket.gethostname()
    _print(args, rc, report)
    return rc


if __name__ == "__main__":
    sys.exit(main())
