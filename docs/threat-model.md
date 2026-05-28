# Threat model

This document enumerates what we are defending against and where each defense lives. Anything not listed here is **out of scope** — write it down before you build it.

## Assets

| Asset | Where it lives | Sensitivity |
|---|---|---|
| Project context (`context.md`, `decisions.md`, `history.md`) | VM | High — contains business strategy, customer data, internal decisions |
| Project registry | VM | Medium — list of project names + owners |
| SSH keys per device | Device `~/.ssh/life-agents` | Critical — grants VM access |
| Trust policy | Device | High — defines what workers can do |
| Worker artifacts (patches, reports) | Worker device → VM → repo | Variable, inherits source repo sensitivity |
| Audit log (`~/life-agents/security.log`) | VM | High — tamper would hide attacks |

## Threats

### T1: Unauthorized device gains VM access

**Vector:** Attacker steals or copies `~/.ssh/life-agents` from a device.

**Mitigations (today, from v0.1):**
- SSH key is device-specific.
- VM has `PasswordAuthentication no`.
- Device revocation: remove fingerprint from `~/.ssh/authorized_keys` on VM.

**Mitigations (v0.2 add):**
- Trust policy on the device alone can't grant write — every write goes through OpenClaw's task queue, which re-verifies.
- Per-adapter audit: any sync from an unknown device shows up in `worker-task.audit.transitions` with no matching `claimed_at`.

**Gaps:** SSH agent forwarding from a compromised device still works. We accept this — covered by physical-security assumption.

### T1.5: MITM / context poisoning during sync

**Vector:** Attacker on the network path between operator's device and the VM intercepts the SSH session, returns a fabricated `registry.json` or `context.md` / `decisions.md`, and seeds the local cache with attacker-controlled "memory" that Mika/Claude/Codex then read as ground truth.

**Mitigations:**
- `scripts/sync.sh` uses strict host-key checking against a dedicated `~/.config/agent-continuity/known_hosts` with `GlobalKnownHostsFile=/dev/null` (no fallback to system trust store).
- The host pin must be bootstrapped explicitly via `scripts/sync.sh --trust-host --confirm-fingerprint SHA256:...` — operator supplies the fingerprint, sync only writes the known_hosts entry if the live keyscan matches what was supplied. No TOFU.
- **Single-algorithm pinning:** only the keyscan line whose fingerprint matches the operator-confirmed value is written to `known_hosts` — other algorithms the server presented in the same exchange are NOT trusted. This blocks the attack where an on-path adversary echoes a real fingerprint for one algorithm and injects a malicious key for another.
- `scripts/doctor.sh` reports `reachability: unpinned` until bootstrap is done, and `reachability: key_mismatch` (ERROR) if the server's key later diverges from the pin (VM key rotation OR active MITM — operator must investigate before re-pinning).
- Re-pinning requires explicit removal of the existing entry (sync refuses to silently overwrite).
- `--trust-host` invoked without `life-agents.json` returns a hard error rather than silently no-op'ing — a security operation that appears to succeed when nothing happened is itself a hazard.

**Gaps:** Operator could be socially engineered into confirming an attacker's fingerprint at bootstrap time. Out-of-band verification (gcloud console, web UI, prior IAP terminal) remains the operator's responsibility.

### T2: Wrong Claude account on a shared device

**Vector:** User A logs out of Claude on a shared Mac; user B logs in. B inherits A's `~/.claude/` and `~/.ssh/` artifacts.

**Mitigations (today, from v0.1):** Account-device binding via `authorized_user` in `life-agents.json`. Step 1.5 in the v0.1 skill blocks any VM connection if accounts don't match.

**Mitigations (v0.2 add):** Same check moves into the adapter layer. The skill is thin — the adapter refuses to start if `authorized_user` mismatch. Documented in `core/schemas/trust-policy.schema.json` as a precondition.

### T3: Worker exceeds its trust level

**Vector:** A worker (Claude or Codex) is instructed via prompt injection (via channel message → Mika → task) to write outside `files_allowed`, push to `main`, exfiltrate secrets.

**Mitigations:**
- `trust_level` resolved at enqueue and re-checked at claim.
- `files_allowed` is a hard refusal in the worker adapter.
- `files_denied` (per-repo, in trust policy) is a hard refusal regardless of `files_allowed`.
- Any task that the worker mutates beyond instruction sets `needs_human_approval: true`.
- Audit captures every transition; un-explained transitions get flagged.

**Gaps:** A worker that is itself adversarial is out of scope — we trust the model + the system prompt. If that changes, see `docs/inbox-guard`-style hardening on the OpenClaw side.

### T4: Channel injection (messaging channel / Telegram → Mika → task)

**Vector:** A vendor or contact sends a crafted message that Mika summarizes into a task that instructs the worker to do something the user didn't authorize.

**Mitigations:**
- This is OpenClaw's problem, not this repo's. We rely on the inbox-guard hardening already deployed (see project memory `project_inbox_guard_security_hardening_2026_05_22`).
- Defense-in-depth: every task carries `source.adapter` and `source.actor`. A task whose source is a non-owner contact gets `require_human_approval_for: ["code-change", "elevated"]` by default.

### T5: Drift between VM-shipped skills and Git-shipped skills

**Vector:** v0.1 auto-rsynced `~/.claude/skills/` from the VM, so VM contents could silently mutate worker behavior.

**Mitigation (v0.2):** Removed. Skills come from Git only. `scripts/doctor.sh` reports any drift between installed skills and the Git revision they were installed from.

### T6: Decision log tampering

**Vector:** Attacker on the VM edits past `decisions.md` entries to hide a malicious decision.

**Mitigations:**
- `decisions.md` is append-only by convention; v0.2 should add a Git mirror of `decisions.md` per project so any edit-in-place leaves a Git diff.
- Backup tarballs (hourly, 30-day retention from v0.1) provide a baseline.

**Status:** Documented gap. Git mirror is M5+.

## Out of scope

- VM compromise at the OS level. We assume GCP + ssh hardening is adequate.
- Compromise of a worker model itself (Claude or Codex behaving adversarially).
- Lateral movement from VM to other GCP projects. Handled by GCP IAM, not by this repo.
