#!/usr/bin/env python3
"""M13.2 SDK smoke test.

Runs entirely in temporary XDG directories. Exercises the SDK's six M9
operations without touching real operator state.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent_continuity import Substrate


def run(cmd: list[str], *, env: dict[str, str], input_text: str | None = None) -> str:
    p = subprocess.run(cmd, input=input_text, capture_output=True, text=True, env=env, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(f"command failed rc={p.returncode}: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p.stdout


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-continuity-sdk-smoke.") as td:
        base = Path(td)
        env = os.environ.copy()
        env.update({
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_STATE_HOME": str(base / "state"),
            "XDG_CACHE_HOME": str(base / "cache"),
        })
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            Path(env[key]).mkdir(parents=True, exist_ok=True)

        s = Substrate(root=REPO_ROOT, env=env)

        checks: list[tuple[str, bool]] = []

        version = s.version()
        checks.append(("version", bool(version)))

        who = s.whoami()
        checks.append(("whoami", who.get("adapter_id") == "continuity-layer-local"))

        context = s.read_context()
        checks.append(("read_context", context.get("schema_version") == "1.0"))

        decision_id = s.append_decision(
            adapter="human",
            repo="sdk-smoke",
            decision="SDK smoke appended a direct decision through Substrate.append_decision.",
            why="M13.2 must prove the Python wrapper routes writes through the canonical decision log.",
            refs=["M13.2", "smoke:sdk"],
        )
        checks.append(("append_decision", len(decision_id) >= 12))

        human_decisions = s.read_decisions(repo="sdk-smoke", adapter="human", limit=10)
        checks.append(("read_decisions", any(d.get("id") == decision_id for d in human_decisions)))

        # Set up the canonical enqueue policy directly. `trust-add` writes
        # M5 bridge grants[], while enqueue still routes through repos[].
        policy_dir = Path(env["XDG_CONFIG_HOME"]) / "agent-continuity"
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / "trust-policy.json").write_text(json.dumps({
            "schema_version": "1.0",
            "default": {
                "max_trust_level": "read-only",
                "allow_kinds": [],
                "deny_kinds": [],
                "require_human_approval_for": ["code-change", "repo-write", "elevated"],
                "allowed_workers": ["claude", "codex"],
                "files_denied": ["**/.env*", "**/*secret*", "**/*credential*"],
            },
            "repos": [
                {
                    "origin": "sdk-smoke",
                    "policy": {
                        "max_trust_level": "read-only",
                        "allow_kinds": ["research"],
                        "deny_kinds": [],
                        "require_human_approval_for": ["code-change", "repo-write", "elevated"],
                        "allowed_workers": ["codex"],
                        "files_denied": ["**/.env*", "**/*secret*", "**/*credential*"],
                        "expires_at": "2099-01-01T00:00:00Z",
                    },
                }
            ],
            "grants": [],
        }), encoding="utf-8")

        instruction = base / "instruction.txt"
        instruction.write_text("Review the SDK smoke fixture and submit a result.\n", encoding="utf-8")
        enqueue_out = run([
            str(REPO_ROOT / "bin" / "agent-continuity"), "worker", "--json", "enqueue",
            "--project", "proj-sdk-smoke",
            "--kind", "research",
            "--target", "codex",
            "--trust-level", "read-only",
            "--instruction", str(instruction),
            "--repo", "sdk-smoke",
            "--source-adapter", "human",
            "--source-actor", "sdk-smoke",
        ], env=env)
        task_payload = json.loads(enqueue_out)
        if not isinstance(task_payload, dict) or not isinstance(task_payload.get("task_id"), str):
            raise RuntimeError(f"enqueue returned unexpected payload: {json.dumps(task_payload, indent=2)}")
        task_id = task_payload["task_id"]

        claimed = s.claim_task(
            adapter="codex",
            as_adapter_id="sdk-smoke",
            kind="research",
            repo="sdk-smoke",
            trust_level="read-only",
        )
        checks.append(("claim_task", isinstance(claimed, dict) and claimed.get("id") == task_id))

        submit = s.submit_result(
            task_id=task_id,
            as_adapter_id="sdk-smoke",
            result={
                "summary": "SDK smoke worker submitted a fixture result.",
                "decisions": [
                    {
                        "decision": "SDK smoke confirmed claim and submit work through Substrate.",
                        "why": "M13.2 must prove the Python SDK can complete the same handoff path as shell and MCP.",
                        "refs": ["M13.2", "smoke:sdk"],
                    }
                ],
            },
        )
        checks.append(("submit_result", submit.get("status") == "completed" and len(submit.get("appended_decision_ids", [])) == 1))

        codex_decisions = s.read_decisions(repo="sdk-smoke", adapter="codex", limit=10)
        checks.append(("worker_decision_writeback", any("Substrate" in d.get("decision", "") for d in codex_decisions)))

        failed = [name for name, ok in checks if not ok]
        for name, ok in checks:
            print(f"{'PASS' if ok else 'FAIL'} {name}")
        if failed:
            print(f"FAILED: {', '.join(failed)}", file=sys.stderr)
            return 1
        print(f"SDK smoke passed ({len(checks)}/{len(checks)})")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
