#!/usr/bin/env bash
# _quickstart_fake_worker.sh — M11.1 tiny fake worker for the OSS quickstart.
#
# Does NOT invoke any real LLM. Claims the given fixture task, marks it
# started, and submits a pre-canned worker-result JSON with one embedded
# decision. The decision's text is self-referential by design — it
# explains why the proof exists.
#
# The worker is a separate script (not inline in quickstart.sh) so the
# OSS visitor can read it directly and see exactly what a "delegated
# worker" looks like at its simplest. ~20 lines of actual logic.
#
# Invoked by `quickstart.sh run-fake-worker`, which provides the sandbox
# env (XDG_CONFIG_HOME / XDG_STATE_HOME / XDG_CACHE_HOME).
#
# Usage:
#   _quickstart_fake_worker.sh <task_id>

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: _quickstart_fake_worker.sh <task_id>" >&2
  exit 64
fi

TASK_ID="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_ID="quickstart-fixture-worker"

# 1. Claim the task. --adapter codex is the canonical adapter brand
# for the quickstart fixture (chosen so the resulting decision is
# attributed adapter: codex per the M11.1 acceptance criteria).
"${SCRIPT_DIR}/worker.sh" --json claim "${TASK_ID}" \
  --adapter codex --worker "${WORKER_ID}" >/dev/null

# 2. Mark the task started. Honest audit: every transition
# (queued -> claimed -> running -> completed) is recorded
# explicitly so the operator can see the full lifecycle.
"${SCRIPT_DIR}/worker.sh" --json start "${TASK_ID}" \
  --adapter codex --worker "${WORKER_ID}" >/dev/null

# 3. Submit a pre-canned worker-result with one embedded decision.
# The submit handler's M8.3 writeback path will validate the decision,
# auto-inject "task:<TASK_ID>" as the first ref, derive adapter from
# task.claimed_by_adapter (codex) and repo from task.input.repo
# (quickstart-project), then append the decision to the canonical log.
RESULT_TMP="$(mktemp -t quickstart-worker-result.XXXXXX)"
trap 'rm -f "${RESULT_TMP}"' EXIT

cat > "${RESULT_TMP}" <<EOF
{
  "task_id": "${TASK_ID}",
  "status": "done",
  "worker": "codex",
  "summary": "Quickstart fixture worker reviewed the assigned task and confirmed the continuity layer's delegated-handoff loop is operational end-to-end.",
  "root_cause": null,
  "findings": [],
  "changed_files": [],
  "tests_run": [],
  "needs_human": [],
  "artifacts": [],
  "decisions": [
    {
      "decision": "Fixture worker reviewed the quickstart task and confirmed the M11 substrate preserves attributed reasoning from a delegated worker.",
      "why": "The quickstart's purpose is to demonstrate that a delegated worker can append a durable decision the operator can read minutes (or months) later, with full provenance and no operator intervention beyond running the script. This decision is that demonstration — its existence in the canonical log IS the proof.",
      "refs": ["M11.1", "doc:docs/north-star.md"]
    }
  ]
}
EOF

"${SCRIPT_DIR}/worker.sh" --json submit "${TASK_ID}" \
  --worker "${WORKER_ID}" --result "${RESULT_TMP}"
