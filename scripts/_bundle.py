#!/usr/bin/env python3
"""bundle.py — adapter bundle export/ingest CLI (M9.1).

Continuity primitive: adapter portability.

Operator-mediated transport for adapters that cannot reach the continuity
layer directly (web agents in chat surfaces, restricted environments).
Encodes the six contract operations as a single JSON object the operator
can hand to / receive from an agent.

Subcommands:
  export    package context + decisions + optional task into a single
            layer-to-adapter JSON bundle. Does NOT mutate the queue;
            the task remains in 'queued' state. bundle_claim metadata
            (task_id + sha256 task_hash + exported_at) lets ingest
            detect changes between export and ingest.
  ingest    apply an adapter-to-layer return bundle: validate envelope,
            validate from_adapter identity, route append_decisions[]
            through the decision log writer, route submit_results[]
            through worker.sh claim/start/submit on the operator's
            behalf using worker id 'bundle:<adapter_id>'.

Failure modes (ingest):
  - bundle envelope invalid          ERROR (no writes)
  - from_adapter identity invalid    ERROR (no writes)
  - bundle_claim.task_hash mismatch  ERROR (queue state changed since
                                     export — refuse to apply a stale
                                     decision/submit)
  - task already claimed by other    ERROR (race lost — refuse to
                                     overwrite another worker's claim)
  - adapter brand outside the worker-capable set
                                     ERROR (prevents unverifiable attribution)
  - draft decision invalid           ERROR (no decisions written; no
                                     submits attempted)

Append-only invariant preserved: ingest never edits decisions.jsonl or
queue files directly; it routes through the canonical writers
(_decisions.append_entries_from_bundle, worker.sh claim/start/submit).

See docs/m9-adapter-pattern.md for the canonical spec.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()

BUNDLE_SCHEMA_PATH = REPO_ROOT / "core" / "schemas" / "adapter-bundle.schema.json"
IDENTITY_SCHEMA_PATH = REPO_ROOT / "core" / "schemas" / "adapter-identity.schema.json"
CONTEXT_SNAPSHOT_PATH = REPO_ROOT / "core" / "context-snapshot.json"
WORKER_SH = REPO_ROOT / "scripts" / "worker.sh"

_XDG_CACHE_HOME = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache"))
QUEUE_ROOT = _XDG_CACHE_HOME / "agent-continuity" / "queue"

BUNDLE_SCHEMA_VERSION = "1.0"
# Bundle ingest accepts worker-capable adapter brands. Web model brands are
# operator-mediated: they can append decisions or submit returned bundle work
# only when trust policy allows the matching target adapter.
BUNDLE_ALLOWED_ADAPTERS: tuple[str, ...] = ("claude", "codex", "chatgpt", "gemini", "grok", "kimi")

# Reuse the canonical schema validator. Doctor stays free of intra-script
# imports per the M7.2/M8.1 pattern, but new scripts CAN import from
# doctor — the dependency direction matters, not absence of coupling.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _doctor import _validate_against_schema
from _decisions import (
    append_entries_from_bundle,
    WorkerDecisionDraftError,
    _iter_entries,
)


# ---------- helpers ----------

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_bundle_id() -> str:
    """Stable, sortable, unique-enough bundle id."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"bundle-{stamp}-{secrets.token_hex(4)}"


def _canonical_task_hash(task: dict[str, Any]) -> str:
    """sha256 hex of canonical JSON of the task. Used to detect any
    change to the task between export and ingest — including audit
    transitions, status changes, or direct file edits. Conservative
    by design."""
    canonical = json.dumps(task, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_task(task_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Find a task by id across queue state directories. Returns
    (task_json, state_name) or (None, None)."""
    if not QUEUE_ROOT.is_dir():
        return None, None
    for state_dir in QUEUE_ROOT.iterdir():
        if not state_dir.is_dir():
            continue
        candidate = state_dir / f"{task_id}.json"
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8")), state_dir.name
            except (OSError, json.JSONDecodeError):
                return None, None
    return None, None


def _load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_worker_sh(args: list[str]) -> tuple[int, str, str]:
    """Run worker.sh with --json and parse the output. Returns
    (rc, stdout, stderr). stdout is left as raw text; caller parses if
    they expect JSON."""
    try:
        proc = subprocess.run(
            [str(WORKER_SH), "--json", *args],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return 127, "", f"could not invoke worker.sh: {e}"
    return proc.returncode, proc.stdout, proc.stderr


# ---------- export ----------

def cmd_export(args: argparse.Namespace) -> int:
    # Context snapshot
    if not CONTEXT_SNAPSHOT_PATH.exists():
        print(
            f"error: {CONTEXT_SNAPSHOT_PATH.relative_to(REPO_ROOT)} missing — "
            f"run scripts/context.sh --write to generate",
            file=sys.stderr,
        )
        return 1
    try:
        context = json.loads(CONTEXT_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not load context snapshot: {e}", file=sys.stderr)
        return 1

    # Decisions (filtered)
    decisions = _iter_entries()
    if args.decisions_repo:
        decisions = [d for d in decisions if d.get("repo") == args.decisions_repo]
    # newest first by ts
    decisions.sort(key=lambda e: e.get("ts", "") or "", reverse=True)
    if args.decisions_limit > 0:
        decisions = decisions[: args.decisions_limit]

    # Task (optional)
    task: dict[str, Any] | None = None
    bundle_claim: dict[str, Any] | None = None
    if args.task:
        loaded_task, state = _find_task(args.task)
        if loaded_task is None:
            print(f"error: task {args.task!r} not found in queue", file=sys.stderr)
            return 1
        if state != "queued":
            print(
                f"error: task {args.task!r} is in state {state!r}, but export "
                f"requires 'queued' (export does not mutate queue state; "
                f"claim happens at ingest)",
                file=sys.stderr,
            )
            return 1
        task = loaded_task
        bundle_claim = {
            "task_id": args.task,
            "task_hash": _canonical_task_hash(task),
            "exported_at": _now_iso(),
        }

    # Default allowed operations: read-only when no task; full handoff when task included.
    if args.allowed_operations:
        allowed = list(args.allowed_operations)
    elif task is not None:
        allowed = ["read_context", "read_decisions", "append_decision", "submit_result"]
    else:
        allowed = ["read_context", "read_decisions"]

    bundle: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle_id": _gen_bundle_id(),
        "direction": "layer-to-adapter",
        "created_at": _now_iso(),
        "for_adapter": {"adapter_id": args.for_adapter},
        "allowed_operations": allowed,
        "context": context,
        "decisions": decisions,
        "task": task,
    }
    if bundle_claim is not None:
        bundle["bundle_claim"] = bundle_claim
    if args.instructions:
        bundle["instructions"] = args.instructions

    # Defense in depth: validate the envelope we built before emitting.
    schema = _load_schema(BUNDLE_SCHEMA_PATH)
    errs = _validate_against_schema(bundle, schema)
    if errs:
        print("internal error: built bundle failed schema validation:", file=sys.stderr)
        for e in errs[:5]:
            print(f"  - {e}", file=sys.stderr)
        return 2

    out_text = json.dumps(bundle, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_text, encoding="utf-8")
        print(bundle["bundle_id"])
        print(
            f"wrote bundle {bundle['bundle_id']} "
            f"(context + {len(decisions)} decisions + "
            f"{'1 task' if task else 'no task'}) to {out_path}",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(out_text)
    return 0


# ---------- ingest ----------

def cmd_ingest(args: argparse.Namespace) -> int:
    bundle_path = Path(args.bundle)
    if not bundle_path.is_file():
        print(f"error: bundle file not found: {bundle_path}", file=sys.stderr)
        return 1

    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: bundle JSON parse failed: {e}", file=sys.stderr)
        return 1

    # 1. Validate envelope against schema
    bundle_schema = _load_schema(BUNDLE_SCHEMA_PATH)
    errs = _validate_against_schema(bundle, bundle_schema)
    if errs:
        print("error: bundle failed envelope schema validation:", file=sys.stderr)
        for e in errs[:5]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # 2. Direction check
    direction = bundle.get("direction")
    if direction != "adapter-to-layer":
        print(
            f"error: ingest requires direction='adapter-to-layer', got {direction!r}. "
            f"layer-to-adapter bundles are EXPORTS, not ingest inputs.",
            file=sys.stderr,
        )
        return 1

    # 3. Validate from_adapter against identity schema
    from_adapter = bundle.get("from_adapter")
    if not isinstance(from_adapter, dict):
        print("error: adapter-to-layer bundle missing from_adapter", file=sys.stderr)
        return 1
    identity_schema = _load_schema(IDENTITY_SCHEMA_PATH)
    errs = _validate_against_schema(from_adapter, identity_schema)
    if errs:
        print("error: from_adapter failed identity schema validation:", file=sys.stderr)
        for e in errs[:5]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    adapter_id: str = from_adapter["adapter_id"]
    adapter_brand: str = from_adapter["adapter"]
    if adapter_brand not in BUNDLE_ALLOWED_ADAPTERS:
        print(
            f"error: M9 bundle ingest supports adapter brands "
            f"{list(BUNDLE_ALLOWED_ADAPTERS)} for bundle-mediated writes; "
            f"got {adapter_brand!r}. Bundle was authored by an adapter brand "
            f"outside the supported attribution set.",
            file=sys.stderr,
        )
        return 1

    bundle_id: str = bundle["bundle_id"]
    append_drafts = bundle.get("append_decisions") or []
    submits = bundle.get("submit_results") or []
    bundle_claim = bundle.get("bundle_claim")

    # 4. Pre-validate submits against bundle_claim correlation + queue state + hash
    submit_plan: list[dict[str, Any]] = []
    if submits:
        if not isinstance(bundle_claim, dict):
            print(
                "error: bundle has submit_results but no bundle_claim — ingest "
                "cannot route a submit without the claim metadata that ties it "
                "to a specific task and export-time hash",
                file=sys.stderr,
            )
            return 1
        claim_task_id = bundle_claim.get("task_id")
        claim_task_hash = bundle_claim.get("task_hash")
        for i, s in enumerate(submits):
            sid = s.get("task_id")
            if sid != claim_task_id:
                print(
                    f"error: submit_results[{i}].task_id ({sid!r}) does not "
                    f"match bundle_claim.task_id ({claim_task_id!r}) — M9.1 "
                    f"bundles cover a single task",
                    file=sys.stderr,
                )
                return 1

        # Look up the task; verify state + hash
        current_task, state = _find_task(claim_task_id)
        if current_task is None:
            print(
                f"error: task {claim_task_id!r} no longer in queue (deleted? "
                f"already cleaned up?). bundle ingest refuses.",
                file=sys.stderr,
            )
            return 1
        current_hash = _canonical_task_hash(current_task)
        if current_hash != claim_task_hash:
            print(
                f"error: task {claim_task_id!r} changed since export — hash mismatch",
                file=sys.stderr,
            )
            print(f"  exported hash: {claim_task_hash}", file=sys.stderr)
            print(f"  current hash:  {current_hash}", file=sys.stderr)
            return 1

        # State must be queued (we'll claim on operator's behalf), or already
        # claimed BY THIS bundle (idempotent retry).
        bundle_worker_id = f"bundle:{adapter_id}"
        if state == "queued":
            pass  # will claim + start + submit
        elif state == "claimed":
            existing = current_task.get("claimed_by")
            if existing != bundle_worker_id:
                print(
                    f"error: task {claim_task_id!r} is claimed by "
                    f"{existing!r}; expected unclaimed or "
                    f"{bundle_worker_id!r} (race lost — another worker took "
                    f"this task between export and ingest)",
                    file=sys.stderr,
                )
                return 1
            # already claimed by us — will start + submit
        else:
            print(
                f"error: task {claim_task_id!r} is in state {state!r}; ingest "
                f"requires 'queued' (or 'claimed' by this bundle for retry)",
                file=sys.stderr,
            )
            return 1

        submit_plan.append({
            "task_id": claim_task_id,
            "task_state": state,
            "submit_results": submits,
            "bundle_worker_id": bundle_worker_id,
        })

    # 5. Dry-run path — surface what would happen without writing
    if args.dry_run:
        report = {
            "dry_run": True,
            "bundle_id": bundle_id,
            "from_adapter": {
                "adapter_id": adapter_id,
                "adapter": adapter_brand,
            },
            "would_apply": {
                "append_decisions_count": len(append_drafts),
                "submit_results_count": len(submits),
                "bundle_claim": bundle_claim,
                "claim_flow": [
                    {
                        "task_id": p["task_id"],
                        "from_state": p["task_state"],
                        "actions": (
                            ["claim", "start", "submit"]
                            if p["task_state"] == "queued"
                            else ["submit"]
                        ),
                        "as_worker": p["bundle_worker_id"],
                    }
                    for p in submit_plan
                ],
            },
        }
        sys.stdout.write(json.dumps(report, indent=2) + "\n")
        return 0

    # 6. Apply: decisions first (M8.3 invariant — append-only writeback
    # never depends on task state). Each section reports applied/rejected.
    report: dict[str, Any] = {
        "bundle_id": bundle_id,
        "from_adapter": {"adapter_id": adapter_id, "adapter": adapter_brand},
        "applied_decisions": [],
        "rejected_decisions": [],
        "applied_submits": [],
        "rejected_submits": [],
    }

    if append_drafts:
        # Repo for bundle-level decisions: try to derive from the task if
        # present, else fall back to "unknown".
        decisions_repo = "unknown"
        if bundle_claim:
            current_task, _ = _find_task(bundle_claim["task_id"])
            if current_task and isinstance(current_task.get("input"), dict):
                decisions_repo = current_task["input"].get("repo") or "unknown"
        try:
            ids = append_entries_from_bundle(
                append_drafts,
                adapter=adapter_brand,
                repo=decisions_repo,
                bundle_id=bundle_id,
            )
            report["applied_decisions"] = ids
        except WorkerDecisionDraftError as e:
            report["rejected_decisions"].append({"error": str(e)})
            print(f"error: append_decisions validation failed: {e}", file=sys.stderr)
            print(json.dumps(report, indent=2), file=sys.stderr)
            return 1

    # 7. Apply submits: claim+start+submit per plan
    for plan in submit_plan:
        task_id = plan["task_id"]
        bundle_worker_id = plan["bundle_worker_id"]
        from_state = plan["task_state"]
        results = plan["submit_results"]

        # Claim (if needed)
        if from_state == "queued":
            rc, out, err = _run_worker_sh([
                "claim", task_id,
                "--adapter", adapter_brand,
                "--worker", bundle_worker_id,
            ])
            if rc != 0:
                report["rejected_submits"].append({
                    "task_id": task_id, "stage": "claim", "rc": rc, "error": err.strip() or out.strip(),
                })
                continue

        # Start
        rc, out, err = _run_worker_sh([
            "start", task_id,
            "--adapter", adapter_brand,
            "--worker", bundle_worker_id,
        ])
        if rc != 0:
            # Start may fail if task is already running (idempotent retry).
            # Probe the task state to distinguish.
            current_task, state_now = _find_task(task_id)
            if state_now != "running":
                report["rejected_submits"].append({
                    "task_id": task_id, "stage": "start", "rc": rc, "error": err.strip() or out.strip(),
                })
                continue

        # Submit — one worker-result per submit_results item
        for j, sr in enumerate(results):
            result_obj = sr.get("result")
            if not isinstance(result_obj, dict):
                report["rejected_submits"].append({
                    "task_id": task_id, "stage": "submit", "index": j,
                    "error": "result is not an object",
                })
                continue
            # Write result to temp file and shell out
            tmp_path = Path("/tmp") / f"m91-bundle-{bundle_id}-{j}.result.json"
            tmp_path.write_text(json.dumps(result_obj), encoding="utf-8")
            try:
                rc, out, err = _run_worker_sh([
                    "submit", task_id,
                    "--worker", bundle_worker_id,
                    "--result", str(tmp_path),
                ])
                if rc != 0:
                    report["rejected_submits"].append({
                        "task_id": task_id, "stage": "submit", "index": j,
                        "rc": rc, "error": err.strip() or out.strip(),
                    })
                    continue
                # Parse JSON output to grab appended_decision_ids
                try:
                    submit_report = json.loads(out)
                except json.JSONDecodeError:
                    submit_report = {"raw_stdout": out.strip()[:200]}
                report["applied_submits"].append({
                    "task_id": task_id, "index": j,
                    "submit_report": submit_report,
                })
            finally:
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass

    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    # Non-zero exit if anything was rejected — even partial application is a
    # surfaceable concern; the operator should see the rejection report.
    if report["rejected_decisions"] or report["rejected_submits"]:
        return 1
    return 0


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Adapter bundle export/ingest CLI (M9.1). Operator-mediated "
            "transport for adapters that cannot reach the continuity layer "
            "directly. See docs/m9-adapter-pattern.md for the spec."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export", help="package context + decisions + optional task into a JSON bundle")
    exp.add_argument("--for-adapter", required=True,
                     help="adapter_id this bundle is exported FOR (e.g. claude-web-2026-05-25-operator)")
    exp.add_argument("--task",
                     help="optional task id to include (task must be in queued/ state; export does NOT mutate queue)")
    exp.add_argument("--decisions-limit", type=int, default=50,
                     help="cap decisions to last N (default 50; 0 = no cap)")
    exp.add_argument("--decisions-repo",
                     help="filter decisions to this repo only")
    exp.add_argument("--allowed-operations", nargs="*",
                     choices=["whoami", "read_context", "read_decisions",
                              "append_decision", "claim_task", "submit_result"],
                     help="override default allowed_operations on the bundle")
    exp.add_argument("--instructions",
                     help="operator-provided guidance for the recipient")
    exp.add_argument("--out",
                     help="write bundle JSON to this file (default: stdout)")

    ing = sub.add_parser("ingest", help="apply an adapter-to-layer return bundle")
    ing.add_argument("bundle", help="path to the bundle JSON file")
    ing.add_argument("--dry-run", action="store_true",
                     help="validate the bundle and report what WOULD be applied; write nothing")

    args = parser.parse_args(argv)
    if args.cmd == "export":
        return cmd_export(args)
    if args.cmd == "ingest":
        return cmd_ingest(args)
    parser.print_help(sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
