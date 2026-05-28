# Troubleshooting

Common failure modes across the adapter surface, with the diagnostic that surfaces them and the canonical remediation. Doctor (`scripts/doctor.sh`) is the first place to look for any of these — most have a check that catches them.

## Stale context snapshot

| | |
|---|---|
| **Symptom** | A fresh agent reads `core/context-snapshot.json` and sees a `source_head_sha` that doesn't match the current commit. The `last_completed` field references work that's already been done. |
| **Doctor** | `[WARN] context snapshot` block shows `STALE (snapshot: <sha>, HEAD: <sha>)` |
| **Cause** | The committed snapshot is older than the current HEAD. This is the natural state immediately after any non-snapshot commit — M7.1 surfaces it as a WARN, not an ERROR, because the snapshot can be slightly behind and still useful. |
| **Fix** | `scripts/context.sh --write` regenerates from current sources. Commit the regenerated `core/context-snapshot.{json,md}` if you want a fresh baseline checked in. |
| **Don't** | Edit the snapshot file by hand. Regeneration overwrites manual edits; the only operator-maintained field (`next_safe_action`) lives separately in `core/context-pinned.json`. |

## Stale pin (`next_safe_action`)

| | |
|---|---|
| **Symptom** | `next_safe_action` says "do M7.1 next" but M7.1 is already shipped. A fresh agent reading the snapshot would do completed work. |
| **Doctor** | `[WARN] context pin` block shows `STALE: references completed milestone(s) M7.1`. |
| **Cause** | Operator updated the pin pointing at an upcoming slice, then shipped that slice without updating the pin to point at the next one. |
| **Fix** | Edit `core/context-pinned.json` to point at the next intended step. Use prose, not M-tags, if the next step is open-ended (M8.2.1's leading-tag check only flags exact tags shipped as commit-subject prefixes). |
| **Note** | Free-form prose without M-tags falls through to OK. The auto-detection is intentionally conservative. |

## Rejected trust policy on claim or submit

| | |
|---|---|
| **Symptom** | `worker.sh claim <task>` exits non-zero with a policy-resolution error. Bundle ingest fails at the claim step. MCP `claim_task` returns null or errors. |
| **Doctor** | `[WARN] trust policy` may show `grants: 0` or `has_default: False`. |
| **Cause** | No grant covers this `(repo, kind, trust_level, adapter)` combination. Default policy is deny. |
| **Fix** | `scripts/worker.sh trust-check <task>` shows exactly which grants were checked and why they didn't match. Then `scripts/worker.sh trust-add ...` to add a grant. |
| **Don't** | Edit `~/.config/agent-continuity/trust-policy.json` by hand if you have multiple grants — `worker.sh trust-add` backs up before writing. |

## Malformed bundle (envelope)

| | |
|---|---|
| **Symptom** | `scripts/bundle.sh ingest <file>` exits with `bundle failed envelope schema validation:` followed by JSON-pointer paths to bad fields. |
| **Cause** | The agent produced a bundle that doesn't match `core/schemas/adapter-bundle.schema.json`. Common: missing required field, extra unknown field (`additionalProperties: false`), wrong direction string, malformed `bundle_claim`. |
| **Fix** | Inspect the error messages — they name the path (e.g. `$.direction`, `$.bundle_claim.task_hash`). Edit the bundle to match the schema; re-ingest. |
| **Don't** | Loosen the schema. The strictness is what makes bundles trustworthy at ingest. |

## Bundle hash mismatch (task changed since export)

| | |
|---|---|
| **Symptom** | `scripts/bundle.sh ingest` exits with `task <id> changed since export — hash mismatch`, printing both hashes. |
| **Cause** | Anything that mutated the task between export and ingest: another worker claimed it, the operator edited the file, the audit grew (claim+release cycle). This is a *stronger* guarantee than a race check — the bundle is a snapshot contract. |
| **Fix** | Re-export and re-run the agent. The agent's prior work isn't lost — `append_decisions[]` from the return bundle is **separate** from `submit_results[]`; the operator can ingest just the decisions by stripping `submit_results` from the bundle and re-ingesting (M9.4 doesn't ship a tool for this, but the schema permits a decisions-only return bundle). |
| **Don't** | Try to bypass by editing the bundle's `task_hash`. The mismatch is the right answer. |

## Wrong adapter (claim ownership)

| | |
|---|---|
| **Symptom** | `worker.sh submit <task> --worker X` exits with `task was claimed by 'Y', submit attempted by 'X'`. |
| **Cause** | The submit's `--worker` doesn't match the task's `claimed_by`. Bundle ingest enforces this implicitly via the `bundle:<adapter_id>` worker-id convention; MCP enforces via `mcp:<adapter_id>`. |
| **Fix** | Use the same worker id that claimed. For bundles, that's `bundle:<adapter_id>` from the bundle's `from_adapter.adapter_id`. For MCP, `mcp:<adapter_id>` from the `as_adapter_id` argument. For direct shell, whatever you passed to `--worker` on `claim`. |
| **Don't** | Try to override the worker check. The check exists because two workers writing to the same task is a data race. |

## Missing task (no longer in queue)

| | |
|---|---|
| **Symptom** | `worker.sh show <task>` returns `task not found`. Bundle ingest fails with `task <id> no longer in queue`. |
| **Cause** | Task was cleaned up, never enqueued, or you're looking at the wrong queue (e.g. a sandbox `XDG_CACHE_HOME` override). |
| **Fix** | `scripts/worker.sh --json list` to see what's actually present. Check `XDG_CACHE_HOME` if you're running with one set. |
| **Don't** | Re-create the task with the same id and try to "resume" the original flow. The audit trail won't reflect the gap. Enqueue a new task and reference the old id in refs if traceability matters. |

## Invalid decisions (worker-result or bundle)

| | |
|---|---|
| **Symptom** | `worker.sh submit` or `bundle.sh ingest` exits with `decision validation failed: decisions[N]: <error>`. |
| **Doctor** | `[ERROR] decisions log` if the on-disk log has malformed entries (rare; usually only happens if someone edited the file by hand). |
| **Cause** | A draft was missing `decision` or `why`, had an unknown field, or had a non-string in `refs[]`. M8.3.1 made this an all-or-nothing reject — one bad draft fails the whole submit/bundle, so the canonical log never contains partial provenance. |
| **Fix** | The error message names the index and the failing field. Fix the draft and retry. |
| **Don't** | Skip the invalid draft and proceed. The submit handler refuses on purpose; bypassing would create silent continuity loss. |

## MCP returned empty / unexpected shape

| | |
|---|---|
| **Symptom** | `mcp.sh tool <name>` fails with `worker.sh list returned unexpected shape: ...` or similar. |
| **Cause** | The underlying script's output format changed. MCP handlers parse specific shapes; an upstream change to `worker.sh --json list` or `decisions.sh list --json` can break the wrapper. |
| **Fix** | Run the underlying script directly and compare to the handler's expectation in `scripts/_mcp.py`. The compat tests at [`../artifacts/M9.2/`](../artifacts/M9.2/) document the shapes M9.2 expects. |
| **Don't** | Patch the MCP handler without updating its compat fixture. The fixture is the contract; drift between manifest, handler, and shell output is exactly what doctor's M9.3 check is supposed to catch. |

## OpenClaw bridge appears broken

| | |
|---|---|
| **Symptom** | Bridge-mediated tasks don't run; `worker_*` MCP tools error out; doctor shows `[WARN] worker bridge` or `[INFO] openclaw-bridge`. |
| **Cause** | `~/.openclaw/workspace/scripts/agent-worker.mjs` is missing, has been edited, or its installed-extension wrapper is gone. |
| **Fix** | Check the `worker_bridge` doctor block for the specific issue (source adapter present? installed extension present? `openclaw` CLI on PATH?). Restore from `*.bak-pre-*` backups if a recent edit broke things. |
| **Note** | The OpenClaw bridge is **optional** for non-OpenClaw users. `[INFO] openclaw-bridge` in M9.3's `m9 adapter portability` block is the expected state on a host without OpenClaw installed — not a failure. |

## See also

- [`../m9-adapter-pattern.md`](../m9-adapter-pattern.md) — what each surface is supposed to do
- [`README.md`](README.md) — pick the right adapter walkthrough
- [`../handoff-vs-continuity.md`](../handoff-vs-continuity.md) — framing for when something feels wrong because you expected the worker queue to be the product
