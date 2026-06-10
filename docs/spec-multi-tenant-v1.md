# Multi-tenant architecture spec (v1.0)

**Status:** Design proposal. No code yet. This is the doc that answers the architectural questions before any implementation begins.

**One-line summary:** Don't build a team agent. Build a team context with per-user agents. The substrate's job is to be the shared layer between many cryptographically-distinct actors, not to host a single shared identity that everyone borrows.

**Scope:** The migration from single-operator (v0.x) to multi-tenant (v1.0). Cryptographic identity, role-based trust, audit log integrity, multi-writer sync, schema migration, and the open-core deployment boundary.

**Non-goals of this spec:**
- UI/dashboard design (separate concern)
- Pricing model (separate doc — this spec stays usable for self-hosters)
- Federation across teams (deferred to v1.1)
- Enterprise SSO integration (deferred to v1.1)
- Anything LLM-driven (the substrate stays at zero LLM dependency)

---

## Why this exists

The substrate today carries an implicit single-operator assumption in every load-bearing surface: one decisions.jsonl with one writer, one trust policy that asks "what can this *adapter* do" (not "what can this *actor* do"), descriptive identity (`adapter: codex`, `author: mau`), append-only-by-convention with no integrity proof, and a sync model (git-memory) that assumes one writer per repo.

That's correct for v0.x. It's incorrect for teams. The migration isn't about adding fields to schemas — it's about the substrate making the same honest promises across multiple actors that it makes for one.

### The "team-persistent" trap

"Team-persistent AI sessions" conflates four very different problems. Conflating them is why team-agent designs usually fail:

| Problem | Status today |
|---|---|
| **Code knowledge** — what the repo does | Solved by git + `CLAUDE.md` hierarchy. Not a substrate problem. |
| **Judgment memory** — why decisions were made, what failed before, conventions | Partially solved by `CLAUDE.md` + auto-memory + ADRs, but personal. This is what the substrate's team layer extends. |
| **Operational continuity** — in-flight tasks, queues, handoffs across machines/people | Solved by the substrate, single-identity (v0.x). v1.0 makes it multi-identity. |
| **Identity & trust** — who is the agent acting as, what is it authorized to do, what audit trail it leaves | Mostly unsolved in the IDE-session world. This is the v1.0 load-bearing addition. |

A team-persistent setup needs all four. Repos give you the first. Substrate v1.0 extends the second and third with cryptographic identity (the fourth). The architecture is layered, not centralized:

```
┌─ Personal layer (per-user, never shared) ───────────────┐
│  ~/.claude/CLAUDE.md, personal memory, shell creds,     │
│  local Ed25519 actor private key                        │
├─ Team layer (shared, governed) ─────────────────────────┤
│  Team memory git repo, team skills (PR-reviewed),       │
│  team continuity queue, shared decision log with        │
│  hash chain + per-actor signatures, team manifest,      │
│  role assertions, audit anchors                         │
├─ Repo layer (already shared via git) ───────────────────┤
│  Code, CLAUDE.md, .claude/skills, .claude/commands,     │
│  ADRs, CODEOWNERS                                       │
└─────────────────────────────────────────────────────────┘
```

The personal/repo split already exists via the `CLAUDE.md` hierarchy. The team layer is what's missing and what v1.0 builds.

Specifically the v1.0 promises:

1. **Actor identity is cryptographic, not descriptive.** A claim that "alice wrote this decision" is verifiable, not trusted by convention.
2. **Audit integrity is provable.** No actor can edit another actor's history without it being detectable.
3. **Trust is role-asserted.** Capability isn't tied to which adapter brand wrote a decision; it's tied to what role the actor holds in the team.
4. **Multi-writer sync converges deterministically.** Two team members editing the same JSONL doesn't produce a state where "the last person to push wins."
5. **Single-tenant compat is preserved.** Existing v0.x installs upgrade without breaking; the team-of-one case stays simple.

---

## The five hardest decisions, answered

These are the load-bearing decisions. Every other decision in this spec follows from them.

### 1. The unit of tenancy is the **team**

Not "organization." Not "operator with multiple roles." Not "project."

A **team** is a named set of human actors who share write access to one decisions log, one project registry, and one trust policy. Each human is one actor. Their AI agents inherit that actor's identity (the agent's decisions are signed with the human's key, attributed with `adapter:` for provenance).

Why team and not organization:
- "Organization" implies hierarchical multi-team scoping. That's a v1.1 problem. For v1.0, one team = one substrate scope. Companies with multiple teams just run multiple substrates.
- Hierarchical tenancy is the kind of design that's wrong if you build it before customers ask for it. Single-team v1.0 is honest; multi-team-org v1.1 can be added cleanly later.

Why team and not "operator with multiple roles":
- mau-as-LV-fiscal and mau-as-substrate-maintainer being two roles in one tenant breaks the "actor identity is cryptographic" promise. Two roles need two key pairs, which means they're operationally two actors. Cleaner to model that as two teams (mau is the only member of both) than as one operator-with-multiple-hats.

Why team and not project:
- Projects are scoped *within* a team (project-registry holds team-scoped projects). A team can have many projects. Cross-project queries within a team are useful (M14.1); cross-project queries across teams are a different problem (multi-tenant analytics, deferred).

### 2. Identity is cryptographic local keys + signed role assertions

**Each actor holds a local Ed25519 keypair.** Private key never leaves the actor's machine. Public key is what other actors verify against.

Identity primitives:
- `actor_id` = fingerprint of public key (e.g. `actor:fp:abc123...`). Stable across machines if the same key is used.
- `pubkey` = the actor's Ed25519 public key. Distributed via the team's signed manifest.
- `team_id` = a UUID generated at team creation. Bound to a team-manifest signed by the founding admin.

**Roles are signed assertions, not config entries.** A `role-assertion` is a small JSON object signed by a team admin's private key, stating "actor X holds role Y in team Z, effective from time T." Verifying any actor's claim of role requires verifying the assertion's signature against a known admin pubkey.

Why this design over alternatives:

- **Why not OAuth/SSO/tokens?** Because they require a central authority. The substrate's identity has always been "no server unless you choose to run one." Tokens break that. Local keys preserve it.
- **Why not a central PKI?** Same reason. Each actor manages their own key. Trust between actors flows through team-manifest signatures, not a central CA.
- **Why Ed25519?** Modern, fast, small signatures (~64 bytes), used by cosign/sigstore (which the substrate already depends on for release verification — single dep family).

What this means operationally:

- A new actor joining a team generates a local keypair, sends their public key to a team admin (via any out-of-band channel), and the admin signs a role-assertion granting them a role.
- The team-manifest (which lists all valid public keys + admin keys) is itself a versioned, signed file synced via git-memory.
- Compromised actor: admin revokes by issuing a new role-assertion with `role: revoked` and signing it. Future verifications see the revocation.
- Lost admin key: this is the recovery problem. v1.0 solves it with **M-of-N admin multisig**: any role-assertion (including key revocation) requires signatures from M of N admins, where M and N are set in the team-manifest. v1.0 ships with M=1, N=1 as the default (single admin) but supports M≥2 for teams that need it.

### 3. Audit integrity is a hash chain

Every decision entry includes `prev_hash: <sha256 of previous entry's canonical body>`. This forms a linear chain. To insert a fabricated decision retroactively, an attacker would have to recompute every subsequent entry's hash — and if any actor has previously synced a later entry, the divergence is detectable.

The hash chain is **per-team, not per-actor**. Multiple actors writing to the same team's log all contribute to one chain. The chain's order is determined by sync semantics (see Decision 4).

Why hash chain and not merkle tree:
- Hash chain is simpler. Merkle gives you efficient inclusion proofs (useful for selective disclosure: "here's proof this one decision is in the log without revealing the rest"). For v1.0, that use case isn't there yet. Linear hash chain is sufficient.
- Merkle remains a v1.x option if selective-disclosure becomes a real need (e.g. compliance audit where you want to prove "decision X was made on date Y" without exposing decisions A through W).

Optional **audit anchoring**: any actor can periodically publish the latest chain head to a third party (the team's git provider counts; a notary service is a paid-tier offering). An anchor with timestamp T proves the chain existed in this state at time T. Useful for non-repudiation in disputes.

### 4. Multi-writer sync is convergent

Two actors append decisions concurrently → both decisions land in the log, ordered by `ts` (the actor's clock at write time). The hash chain is recomputed during merge so both decisions appear in the canonical chain. There is no "last write wins" because the JSONL is genuinely append-only — concurrent appends to the SAME log don't collide; they interleave.

The merge algorithm:

1. `git fetch` from origin.
2. Read local-only decisions (entries we have, origin doesn't).
3. Read origin-only decisions (entries origin has, we don't).
4. Merge into a single sorted-by-ts list.
5. Recompute `prev_hash` on each entry in the merged order. Note: this changes the *chain* but not the *content* of decisions — each decision's intrinsic id (sha256 of body excluding prev_hash) is stable.
6. Commit the merged log + signed merge anchor.
7. Push.

If two actors generate decisions with identical `ts` (clock skew, batched writes), tiebreak by `actor_id` lexicographic order. Deterministic.

The `id` of each decision is `sha256(canonical body without prev_hash)`. That stays stable across merges. The `prev_hash` is a property of the *chain*, not the *decision*. Two different teams could have the same decision (same id) in different positions in their chains.

This means decisions are content-addressed (stable) and chain-positioned (mutable on merge but verifiable). Verifying a chain doesn't require knowing decisions; verifying a decision doesn't require knowing the chain. Clean separation.

### 5. Single-tenant installs become "team of one"

Existing v0.x installs migrate to v1.0 by being treated as a one-actor team:
- A `team_id` is generated locally (`personal-<machine-id>` or operator-supplied).
- An `actor_id` is derived from a locally-generated Ed25519 key.
- All existing decisions are rewritten with `team_id` and `actor_id` set, and a chain is computed across them in `ts` order.
- The hash chain begins from a known-zero `prev_hash` for the first entry.

This is a one-time migration on first run of v1.0. Idempotent: re-running is a no-op. Reversible: a `migrate --rollback` keeps the v0.x format archived.

The operational impact for an individual operator: their decisions log gains four fields (`team_id`, `actor_id`, `prev_hash`, `signature`) but nothing else changes. `decisions add` is unchanged. `read_decisions` is unchanged. The substrate keeps doing what it did, with cryptographic integrity added underneath.

---

## Schema changes

All schema versions bump from `1.0` → `2.0`. The migration framework (existing `agent-continuity migrate`) handles the transition.

### decision-entry.schema.json (v2.0)

```json
{
  "schema_version": "2.0",
  "id": "<sha256 of canonical body>",
  "ts": "<ISO-8601 UTC, signer's clock at write time>",
  "team_id": "<uuid>",
  "human_actor_id": "<actor:fp:...>",
  "device_key_id": "<device-key fingerprint>",
  "adapter": "<claude|codex|...>",
  "author": "<optional human-readable label>",
  "repo": "<string>",
  "decision": "<≤120 chars>",
  "why": "<≤200 chars>",
  "refs": ["<string>", "..."],
  "manifest_version_observed": "<integer, manifest_version at sign time>",
  "role_assertions_head_observed": "<sha256 of role-assertions.jsonl tail>",
  "signer_consent": "<one of: implicit | explicit-confirmed | reviewed-merge>",
  "device_signature": "<base64 Ed25519 signature of canonical body by device_key>"
}
```

**Critical: `prev_hash` is NOT in the decision entry.** Chain position lives in a separate signed structure (see "Chain links" below). The decision's signature covers only the content the signing actor authored and could meaningfully consent to — never anything that a future merger might recompute.

**Identity model (corrected):**
- `human_actor_id` — stable identity for attribution, role, audit. Never rotates as long as the human is in the team.
- `device_key_id` — per-device Ed25519 keypair fingerprint. Rotates when a device is decommissioned or compromised. The manifest binds each `human_actor_id` to a set of currently-valid `device_key_id`s.

A decision signed by a `device_key_id` is attributed to the `human_actor_id` that the manifest currently binds it to. Revoking a single device revokes only that key; revoking an actor revokes all their device keys.

**Authorization-against-clock-skew (corrected):**
- Every decision records `manifest_version_observed` and `role_assertions_head_observed` at sign time.
- Authorization verifies: at the recorded manifest version + assertions head, did the signing actor's role permit this write?
- A backdated decision can claim any `ts` it wants, but the embedded manifest/assertions observation can't be retroactively faked — they have to point at a real (verifiable) past state of the team manifest and assertions log. If a compromised actor signs a decision with `manifest_version_observed: N` and the latest non-compromised observation by other actors is `N+5` referencing a revocation of this actor, the backdated decision is rejected as "signed against a manifest version inconsistent with team-observed history."

**Consent semantics:**
- `signer_consent: implicit` — produced by an automated session signer without explicit human confirmation. Acceptable for free-write entries.
- `signer_consent: explicit-confirmed` — the human confirmed this specific decision via the CLI/UX before the signer signed it. Required for reviewed-write entries and team-wide assertions.
- `signer_consent: reviewed-merge` — a senior actor signed during a merge operation acknowledging this decision's inclusion. Used in convergent sync.

A device signature only proves "a process with access to this device's private key produced this." Whether the human consciously approved is asserted by `signer_consent` and enforced by the local signer policy (see "Signer policy" section below).

New fields: `team_id`, `human_actor_id`, `device_key_id`, `manifest_version_observed`, `role_assertions_head_observed`, `signer_consent`, `device_signature`.
Removed fields: `prev_hash` (moved to chain-link structure), legacy single `actor_id` (split into human + device).
Changed fields: `id` formula excludes only the `device_signature` itself (so id stays stable; recompute only if content changes).

### NEW: chain-link.schema.json (v1.0)

Chain position is a separate signed structure from the decision itself. This is the key correction that lets decisions stay immutable while merges still produce a verifiable canonical order.

```json
{
  "schema_version": "1.0",
  "type": "chain-link",
  "team_id": "<uuid>",
  "decision_id": "<sha256 of the decision being placed>",
  "chain_position": "<integer, monotonic per team>",
  "prev_chain_link_hash": "<sha256 of previous chain-link body, or zero for genesis>",
  "linked_by_human_actor_id": "<who placed this in the chain>",
  "linked_by_device_key_id": "<which device signed the link>",
  "linked_at": "<ISO-8601 UTC>",
  "merge_context": {
    "is_merge_link": "<bool>",
    "source_actors": ["<actor_id>", "..."],
    "git_commit_observed": "<sha of git-memory repo HEAD at link time>"
  },
  "link_signature": "<base64 Ed25519 of canonical body by linker's device_key>"
}
```

Chain-links live in `<memory-repo>/chain-links.jsonl`. The chain is verified by replaying chain-links in `chain_position` order, validating each link's `prev_chain_link_hash` against the previous entry and each link's signature against the linker's currently-valid device key.

Key property: **a decision's signature is invariant once written.** Only its chain-link is recomputed during merge. The original decision-author's signature stays valid forever; the merge operation produces NEW chain-link signatures by the merger over a NEW canonical order. Both are verifiable independently.

Quarantine: if a chain-link or decision fails signature verification during sync, the entry is moved to `<memory-repo>/quarantine.jsonl` with:

```json
{
  "quarantined_at": "<ISO-8601 UTC>",
  "quarantined_by_human_actor_id": "<who detected the failure>",
  "rejection_reason": "<signature-invalid | unknown-signer | manifest-skew | ...>",
  "source_git_commit": "<sha of the commit that introduced the bad entry>",
  "original_entry_hash": "<sha256 of the entry as observed>",
  "original_entry": "<the raw entry, preserved verbatim>"
}
```

Quarantine is preservation, not deletion. In security, invalid signatures are evidence.

### worker-task.schema.json (v2.0)

Add:
- `team_id` (string) — scopes the task to a team
- `created_by_actor` (actor_id) — who dispatched it
- `claimed_by_actor` (actor_id, nullable) — who claimed it
- `submitted_by_actor` (actor_id, nullable) — who submitted the result

The existing `trust_level` field stays. It now interacts with role assertions: a task with `trust_level: high` can only be claimed by an actor whose role in the team includes a capability that permits high-trust execution.

### trust-policy.schema.json (v2.0)

Restructure. The current single-tenant trust policy keys per-adapter (`{adapter: claude, can_*: true/false}`). The v2.0 policy keys per-role:

```json
{
  "schema_version": "2.0",
  "team_id": "<uuid>",
  "roles": {
    "admin": {
      "capabilities": ["read_all", "write_decisions", "claim_high_trust", "issue_role_assertion", "revoke_actor"]
    },
    "member": {
      "capabilities": ["read_all", "write_decisions", "claim_medium_trust"]
    },
    "observer": {
      "capabilities": ["read_all"]
    }
  },
  "adapter_constraints": {
    "claude": {"can_read": true, "can_write": true},
    "codex": {"can_read": true, "can_write": true},
    "...": "..."
  }
}
```

Adapter constraints stay (still useful for "this adapter is allowed to do this kind of thing at all"). Roles add who-can-do-what on top. Authorization is the intersection: an actor can do X iff their role's capabilities include X *and* their current adapter's constraints permit X.

### project-registry.schema.json (v2.0)

Add `team_id` to each project entry. Projects are team-scoped. A single substrate install can hold multiple teams (rare, but supported); each team has its own project list.

### context-snapshot.schema.json (v2.0)

Add `team_id`. Context snapshots are team-scoped. The 60-second snapshot answers "what's happening in *this team's* projects."

### NEW: team-manifest.schema.json (v1.0)

The manifest is a **registry**, not a state authority for roles. It lists who's in the team and which device keys are bound to which human actor. Current role is derived solely from `role-assertions.jsonl`. Conflating them was a mistake; the corrected design keeps the two cleanly separate.

```json
{
  "schema_version": "1.0",
  "team_id": "<uuid>",
  "created_at": "<ISO-8601 UTC>",
  "founding_admin_human_actor_id": "<actor:fp:...>",
  "actors": [
    {
      "human_actor_id": "<actor:fp:...>",
      "display_name": "<optional human-readable label>",
      "device_keys": [
        {
          "device_key_id": "<device-key fingerprint>",
          "pubkey": "<base64 Ed25519 public key>",
          "device_label": "<e.g. mau-macbook-pro>",
          "added_at": "<ISO-8601 UTC>",
          "added_by_admin_signature": "<base64>",
          "revoked_at": "<ISO-8601 UTC, nullable>"
        }
      ]
    }
  ],
  "admin_set": [
    "<human_actor_id>", "..."
  ],
  "multisig": {
    "M": 1,
    "N": 1
  },
  "role_assertions_head": "<sha256 of role-assertions.jsonl tail at manifest_version>",
  "manifest_version": "<integer, increments on every change>",
  "manifest_signature_set": [
    {
      "signed_by_human_actor_id": "<admin>",
      "signed_by_device_key_id": "<device>",
      "signature": "<base64>"
    }
  ]
}
```

The team-manifest lives at `<memory-repo>/team-manifest.json`. What it answers: "who's in the team, what device keys are valid for each person, who are the admins, what's the multisig threshold." What it does NOT answer: "what role does this person currently hold." That's role-assertions territory.

`role_assertions_head` is a pointer to the role-assertions log tail at the manifest's version. It anchors the two structures: changing roles requires both appending to role-assertions.jsonl AND bumping `manifest_version` with an updated `role_assertions_head`. This makes "manifest skew" detectable — if a decision claims `role_assertions_head_observed: X` but the actual manifest at `manifest_version_observed` recorded `role_assertions_head: Y`, the decision was signed against an inconsistent view and is rejected.

Manifest changes require M-of-N admin signatures collected in `manifest_signature_set` (M=1, N=1 default; raise for teams that need multi-admin recovery).

### NEW: role-assertion.schema.json (v1.0)

```json
{
  "schema_version": "1.0",
  "type": "role-assertion",
  "team_id": "<uuid>",
  "subject_actor_id": "<actor:fp:...>",
  "subject_pubkey": "<base64>",
  "role": "<admin|member|observer|revoked>",
  "effective_from": "<ISO-8601 UTC>",
  "issued_at": "<ISO-8601 UTC>",
  "issued_by_actor_id": "<actor:fp:...>",
  "signature": "<base64>"
}
```

Role assertions are append-only in `<memory-repo>/role-assertions.jsonl`. The current role of an actor is determined by the most recent valid assertion.

### NEW: audit-anchor.schema.json (v1.0) — optional

```json
{
  "schema_version": "1.0",
  "type": "audit-anchor",
  "team_id": "<uuid>",
  "chain_head_id": "<sha256 of last decision body>",
  "chain_head_position": "<integer, count of decisions at anchor time>",
  "anchored_at": "<ISO-8601 UTC>",
  "anchored_by": "<self|notary-service-url>",
  "anchor_signature": "<base64>"
}
```

Audit anchors live in `<memory-repo>/audit-anchors.jsonl`. Self-anchored by team admins is free. Third-party-notarized is the paid-tier service.

---

## MCP surface impact

All six adapter-contract operations gain actor context. Two options for how the caller asserts identity:

**Option A: per-call actor signature.** Every `tools/call` includes a `caller_actor_id` + `caller_signature` over the request. Heavy on the wire, strong on integrity.

**Option B: session-bound actor.** `initialize` includes the caller's actor_id; subsequent calls inherit it; the server validates writes against the session's actor. Lighter, requires session integrity (stdio MCP is already process-bound, so this is fine).

**Recommendation: Option B for v1.0, with explicit signer-policy gates.** stdio MCP is already a trusted boundary (the process is launched by the operator's own client). Re-asserting identity on every call adds friction without security benefit in that context. Per-call signatures become relevant if/when MCP-over-network ships.

But session-bound identity has a subtler honesty problem that must be made explicit: **a session signature proves "a process with access to the device's private key produced this," not "the human consciously approved."** The substrate must not pretend otherwise. The local signer enforces consent semantics via policy gates, recorded in each decision's `signer_consent` field:

| Decision type | Signer consent required | Default policy |
|---|---|---|
| Free-write (personal feedback, context snapshot, project registry update) | `implicit` | Session signs automatically |
| Reviewed-write (decision claiming team-wide binding, convention, policy change) | `explicit-confirmed` | Session prompts the human via the operator's client; signature is rejected if no explicit confirmation reaches the signer |
| Team-wide assertion (role assertion, manifest change) | `explicit-confirmed` AND multisig if M>1 | Admin must explicitly confirm; multisig collected before signing |
| Merge canonical ordering | `reviewed-merge` | Senior actor performing the merge signs as part of the explicit `git-memory sync` operation |

The signer policy lives in `~/.config/agent-continuity/signer-policy.json` (per device) and is part of the per-role `.claude/settings.json` profile. A member's session attempting to sign a reviewed-write entry without explicit confirmation produces a signer error, not a silently auto-signed decision. The "human consciously approved" property is now an enforceable invariant on writes that bind the team, not an unspoken assumption.

Per-tool impact:

- `whoami` — returns the substrate's identity *as seen by this caller*: `human_actor_id`, current role (derived from role-assertions head, not manifest), the team manifest version, the device key fingerprint signing this session.
- `read_context` — filtered to caller's team.
- `read_decisions` — filtered to caller's team. Admin role can pass `--cross-team` (deferred to v1.1).
- `append_decision` — server determines required `signer_consent` based on decision type; obtains consent if needed; signs decision content with the device key; attributes to `human_actor_id`; appends a chain-link signed by the same device key; validates the actor's role at the observed manifest/assertions state.
- `claim_task` — validates role permits the task's trust_level. Records both `human_actor_id` and `device_key_id`.
- `submit_result` — validates submitting `human_actor_id` matches the claiming actor (or has admin override). Device key may differ if the human submitted from a different machine than they claimed from.

---

## Multi-writer sync impact

`git-memory sync` already pulls + rebases + pushes. v1.0 extends it. The corrected algorithm never mutates a decision-entry; it only appends new chain-link entries that establish a merged canonical order.

1. **Pull**: `git pull --rebase` as today, but the rebase scope excludes `decisions.jsonl`, `chain-links.jsonl`, and `quarantine.jsonl` (all three are content-addressed append-only logs whose merge semantics are application-level, not git-level).

2. **Diverged-state detection**: if local has chain-links the remote doesn't and vice-versa, enter convergent-merge mode.

3. **Validate every decision and chain-link** (local + remote) against the manifest active at the entry's recorded `manifest_version_observed`. For each entry:
   - Look up the device key fingerprint in the manifest at that version.
   - Verify the signature against that pubkey.
   - Confirm the device key wasn't already revoked at that manifest version.
   - Confirm `role_assertions_head_observed` matches the manifest's view at that version.

4. **Quarantine on failure**: any entry that fails verification is moved to `quarantine.jsonl` with full provenance (rejection reason, source git commit, raw entry, observed-by). The original entry is NEVER deleted — invalid signatures are evidence of tampering or corruption and must be preserved for incident response.

5. **Interleave decisions** by `ts` with `(human_actor_id, device_key_id)` lexicographic tiebreak. The decision content is untouched; only the chain-link ordering is computed.

6. **Append new chain-links** for any decisions that don't have a chain-link in the merged ordering yet. Each new chain-link is signed by the merger's device key with `is_merge_link: true` and `source_actors` listing every distinct actor whose decisions are being merged. The merger's chain-link signature is "I observed these decisions in this order at this manifest state" — distinct from the original decision authors' content signatures.

7. **Sign merge audit-anchor**: append a signed `audit-anchor` entry recording the final chain head + chain position + git commit observed.

8. **Push**: standard.

If two actors merge concurrently, the same convergence algorithm applies recursively. The merge result is deterministic regardless of merge order because `(ts, human_actor_id, device_key_id)` gives a total order on decisions, and chain-link recomputation is deterministic.

**What stays auditable**: every original decision signature remains valid forever. Any verifier can re-fetch the decisions, validate signatures against the manifest at each decision's `manifest_version_observed`, and confirm the chain-link order. The merge doesn't invalidate or rewrite history; it appends a signed canonical ordering on top of immutable signed content.

**What's no longer assumed**: that all actors agree on clock order. The chain-link merger is now the trust anchor for "this is the canonical merged order at the time of this sync," signed by their device key. Disputes about ordering become traceable to specific merge events with named human accountability.

**Backdating defense**: an actor who tries to backdate a decision (e.g. to appear pre-revocation) signs it with their current device key but is forced to record `manifest_version_observed` at sign time. If their current observed manifest version already shows their revocation, the decision is rejected on every verifier's machine, regardless of the claimed `ts`. The attack surface for clock-skew exploits collapses to "the actor's local manifest was somehow stale at sign time" — which is detectable because every other actor's view of the manifest contradicts the backdated claim.

---

## Authorization model

Every write (decision, task transition, role assertion, manifest edit) is authorized by:

1. **Signature validation**: the writer's signature must verify against a public key listed as current in the team manifest.
2. **Role check**: the writer's current role (per most recent role-assertion) must include the capability for this operation.
3. **Adapter constraint** (existing): the adapter brand must permit this operation per the adapter-constraints section of trust-policy.

Read operations require only signature validation + team membership. No per-decision read ACLs in v1.0 (deferred to v1.x if needed).

Admin operations (issue role-assertion, revoke actor, change M-of-N multisig) require:
1. Signature by an admin (verified against manifest).
2. If multisig M>1, signatures by M admins assembled into a single signed operation.

This is the only place multisig matters for v1.0. Day-to-day writes are single-actor signed.

### Three failure modes the authorization model has to survive

These are the real reasons multi-tenant agent systems break:

**1. Memory poisoning.** A new member writes "always skip validation in module X" because it was situationally true once; future sessions treat it as gospel. Authorization-on-write doesn't catch this — the member has write permission, the assertion just happens to be misleading.

The fix is **review, not access control**. v1.0 supports two write paths for team memory:

- **Free-write** entries (context snapshots, project registry updates, feedback the member is explicit about being personal): land on `main` immediately, signed by the actor.
- **Reviewed-write** entries (decisions with `decision:`, `committed:`, `released:`, `tagged:` prefixes; feedback claiming to be team-wide): land on a `pending-review` branch in the team-memory repo. A senior actor (role with `review_team_writes` capability) reviews and merges to `main`.

The "what counts as reviewed" set is configurable per-team in `trust-policy.json` under a new `review_required` field (list of decision-prefix patterns or ref patterns). Default: decisions whose `refs` include `team-wide` or `convention` or `policy`.

This is the substrate's analog of `CODEOWNERS` for memory. Personal writes are free. Writes that claim to bind the team go through review.

**2. Action blast radius.** A new member's session runs `git push --force` on `main`, drops a table, or leaks creds. Authorization-on-write protects the memory log but doesn't protect the world.

The fix is **role-scoped tool permissions**. v1.0 ships a per-role `.claude/settings.json` profile pattern. A team's `trust-policy.json` declares which permission profile each role inherits:

```json
{
  "roles": {
    "senior": {
      "capabilities": ["read_all", "write_decisions", "review_team_writes", "claim_high_trust"],
      "claude_settings_profile": ".claude/profiles/senior.settings.json"
    },
    "member": {
      "capabilities": ["read_all", "write_decisions_unreviewed", "claim_medium_trust"],
      "claude_settings_profile": ".claude/profiles/member.settings.json"
    }
  }
}
```

The senior profile permits destructive tools with approval; the member profile denies them outright. `agent-continuity connect codex --as-role member` writes the member profile to the appropriate location; promotion is a reviewed admin event, not a self-declared choice.

Existing single-operator installs get the senior profile by default (they ARE the senior).

**3. Drift.** Five people edit `CLAUDE.md`, skills, conventions with no review; they become incoherent within a quarter.

The fix is **treat shared agent artifacts as code**. v1.0 documents (but doesn't enforce — it's a workflow, not a runtime check):

- `CLAUDE.md` and `.claude/skills/` live in the repo, governed by `CODEOWNERS`.
- Skill changes require PR review by the role with `review_team_skills` capability.
- A skill should have an example invocation that runs in CI when feasible (substrate doesn't ship CI tooling; it documents the practice).

The substrate's contribution to drift prevention is making sure every team-skill load + invocation is attributed: `decisions add` records which skill was loaded by which session by which actor. Drift becomes traceable; the substrate doesn't enforce its absence.

---

## Where to actually start

Don't build the team tier in the abstract. Stage it from where it already exists:

1. **Pick one real repo with 2+ humans on it.** A real team, not a hypothetical. The first customer is the substrate's most important design input — they'll surface assumptions you couldn't have predicted.
2. **Multi-tenant scaffolding first, cryptographic identity second.** Phase 1 below adds `team_id` / `actor_id` / role to schemas and trust policy. Phase 2 adds the keys and signatures. The scaffolding can be validated against a 2-person team using descriptive identity before the cryptographic layer is ready.
3. **Stand up team memory as a git repo** the way you already do for personal memory. Three files to start: `decisions.jsonl` (existing format), `conventions.md` (free-write team conventions), `incidents.md` (postmortems). `CODEOWNERS` protects them. Sessions read on start; reviewed-writes go through PR.
4. **Define two roles minimally: senior and member.** The three-role spec above (`admin`, `member`, `observer`) is the v1.0 capability surface; the two-role default (`senior` = admin+review-capable; `member` = restricted writes) is what a real first team uses. Observer comes in when external auditors or compliance reviewers show up.
5. **Only then decide whether to centralize.** A git repo + signed commits + role assertions gets you ~80% of the audit story for free. Hosted services (notary, identity verification, conflict resolution coordinator) are paid-tier upgrades when teams hit the limits of self-hosted.

The strategic frame: the substrate doesn't make a team agent. It makes a team context with per-user agents. Each engineer's session acts as that engineer (their git author, their keypair, their permission scope), but pulls from and contributes to the shared layer.

If you copied the VM-agent / Mika model into IDE sessions, you'd get one shared "team-claude" account doing commits — which is an audit nightmare the moment the new guy joins and a governance disaster the moment they leave. v1.0 explicitly rejects that pattern.

---

## Migration path (v0.x → v1.0)

**Phase 0**: This spec, plus operator review + approval.

**Phase 1 — local cryptographic identity (3-4 weeks)**:
- Add Ed25519 keypair generation/storage to `agent-continuity` (under `$XDG_CONFIG_HOME/agent-continuity/actor-key/`)
- Schema v2.0 for decision-entry (add fields, keep backward-readable)
- `decisions add` signs decisions; `read_decisions` validates signatures
- Migration tool: v0.x → v1.0 in-place rewrite (creates a "personal" single-actor team)
- Smoke: round-trip an existing decisions.jsonl through migration; verify hash chain is intact and re-readable.

**Phase 2 — multi-writer sync with hash chain (4-6 weeks)**:
- Hash chain computation + verification in `_decisions.py`
- Merge algorithm in `_git_memory.py`
- Smoke: two simulated actors writing concurrently → merge produces deterministic chain
- Docs: explain chain integrity model + how merge works

**Phase 3 — team manifests + role assertions + authorization (6-8 weeks)**:
- `team` subcommand (`agent-continuity team init`, `team invite`, `team accept`, `team revoke`)
- team-manifest.json sync via git-memory
- role-assertions.jsonl
- Authorization enforcement in `_decisions.py`, `_worker.py`, MCP server
- Smoke: full team flow (admin creates team, invites member, member writes decision, admin revokes member, verification rejects post-revocation writes)
- Docs: team setup guide

**Phase 4 — audit anchoring (2-3 weeks)**:
- Self-signed audit anchors (free, automatic on every sync)
- Notary anchor protocol (spec only; service is paid-tier deferred)
- Docs: integrity model

**Phase 5 — v1.0 release**:
- Tag v1.0 with all of the above merged + stable
- Cosign signature, reproducible build, etc. (existing release process)
- Migration guide for v0.x operators (point at `migrate v1` command)

**Total realistic budget: 4-5 months** of focused part-time work, or 6-8 weeks of full-time. The "one weekend" framing common in early-stage multi-tenant pitches is off by ~50x because it conflates "add team_id fields" (genuinely a weekend) with "make the cryptographic + audit + sync + role + recovery story actually hold up" (months).

What IS a weekend: this spec. Validating it against a first real team before implementation starts is the right next step.

---

## Open-core deployment boundary

The architecture above is **entirely open source**. Schemas, signing, verification, hash chain, role assertions, authorization — all in the v1.0 release. A team that wants to self-host the substrate runs the open-source code and uses git (their own GitHub, GitLab, or self-hosted Forgejo) for memory sync.

The paid tier is operational services, not capabilities:

| Tier | What you get |
|---|---|
| **Open source (free)** | Everything in this spec. Substrate, signing, verification, hash chain, role assertions, multi-writer sync, audit anchors (self-signed). Run on your own machines with your own git provider. |
| **Hosted identity service (paid)** | Cryptographic identity verification as a service: federated trust mesh so actor identities are verifiable across teams without per-pair key exchange. Lost-key recovery flow with optional escrow. |
| **Hosted audit anchoring (paid)** | Third-party notarization of chain heads. Non-repudiable proof "the chain existed in this state at this time." Useful for compliance / dispute resolution. |
| **Hosted sync coordinator (paid)** | Conflict resolution UI when concurrent merges produce ambiguous semantics. Live observability of chain state across team members. |
| **Support, SLA, indemnity (paid)** | Standard enterprise checkbox stuff. |

The line is: **anything an individual operator can run on their own machine + their own git repo stays free. Anything requiring shared infrastructure between multiple operators is paid.**

This boundary is enforceable because:
- The paid services are genuinely shared-infra-shaped (no single operator runs a notary or a federated identity mesh).
- The open-source product is fully useful standalone (team-of-one self-hosting works without paying for anything).
- Pricing maps to operational cost (running a notary service costs money; running your own substrate doesn't).

---

## Open questions (intentionally unresolved)

1. **Federation across teams (v1.1).** How does actor X in team A reference a decision made in team B? Cross-team refs are a real use case (e.g. open-source projects with contributors across companies). v1.0 punts on this; v1.1 needs a federation protocol.

2. **Selective disclosure (v1.x).** A team admin wants to share *just one decision* with an external auditor without exposing the rest of the log. Merkle proofs solve this. Whether v1.x needs it depends on compliance demand.

3. **Role inheritance.** Can a role grant a subset of capabilities to another role? v1.0 says no (flat role list per team). v1.x may need it for larger teams.

4. **Time authority.** Backdating attacks via actor clock skew are now bounded by `manifest_version_observed` + `role_assertions_head_observed` — an actor can't claim a past `ts` while observing a future manifest state. The residual risk is that the actor's local manifest is genuinely stale (network partition, offline operation), which would let them sign decisions inconsistent with the team's current view. v1.0 accepts this — partition healing produces an authoritative manifest at sync time, and decisions signed against pre-partition state get reconciled or quarantined. v1.x may add NTP-attested timestamps or external time anchors if the offline-actor case becomes adversarial.

5. **Key rotation cadence.** Best practice is to rotate keys periodically. v1.0 supports per-device key rotation via a manifest update: revoke the old `device_key_id`, add a new one, both signed by either the human's other active device or by an admin if the rotating device is the only one. UX for this is deferred to phase 3+. Decisions signed by a revoked device key remain valid (the signature still verifies against the historical manifest version they recorded); future decisions must use a currently-valid device key.

6. **Multi-device per actor — RESOLVED.** Earlier draft conflated "actor" with "device key" and produced `mau-on-laptop` and `mau-on-desktop` as separate actor identities. The corrected model is `human_actor_id` (stable) + multiple `device_key_id`s bound to that human in the manifest. Decisions are signed by a device key but attributed to the human. Revoking a device revokes only that key; revoking the human revokes all their device keys. Audit and accountability are now consistently "human as actor" with device as the signing instrument.

7. **Backward compat for v0.x readers.** A v0.x install reading a v1.0 decisions.jsonl — does it fail loudly or skip unknown fields? v1.0 spec says: append v2.0 schema but keep v1.0 fields parseable. v0.x readers ignore the new fields silently. The cost: v0.x can't verify chain integrity but can still read decisions. The migration tool offers a `--strict-v2` mode that refuses v1-style entries for teams that want to enforce post-migration purity.

8. **Migration of pre-multi-tenant signatures.** Existing single-tenant decisions have no signatures at all. Migration generates a "v0-legacy" wrapper signature using the operator's newly-generated device key, with `signer_consent: implicit` and a marker `migrated_from_v0: true`. These entries are signed but flagged as "pre-multi-tenant content; the signature attests to migration provenance, not original authoring intent."

---

## What's NOT in this spec on purpose

- **Specific cryptographic library choice.** Ed25519 is the algorithm. Library choice (PyNaCl, cryptography, dedicated cosign integration) is an implementation detail decided when phase 1 starts.
- **Exact CLI surface for `team` subcommand.** Sketched but not specified to the flag level. UX feedback during phase 3 will shape it.
- **Migration UX details.** The `migrate v1` command exists conceptually; its exact prompts and rollback semantics are an implementation concern.
- **Hosted service architecture.** The paid tier services are mentioned but not designed here. That's a separate spec that depends on demand validation (do teams actually want these?).
- **Pricing.** Out of scope. The spec is architecture; pricing is product.

---

## Definition of done

v1.0 ships when:

1. All schema migrations are in place and tested.
2. Cryptographic identity works locally for the team-of-one case (existing single-operator installs migrate cleanly).
3. Multi-writer sync produces deterministic merged chains across two simulated actors.
4. Role assertions enforce authorization on every write path.
5. Audit anchors (self-signed) attach to every sync.
6. Existing public-tier features (MCP, connect, watcher, git-memory) all work under multi-tenant.
7. Migration guide + team setup docs are written.
8. Smoke suite covers single-actor compat, two-actor concurrent writes, role revocation, key rotation, chain integrity verification.

v1.0 does NOT require hosted services to exist. Those ship when (and if) teams ask for them.

---

## Revision history

**v1.0-rev2 (this version)** — security review pass addressing six findings from an independent reviewer:

| Finding | Severity | Fix |
|---|---|---|
| Signature covered `prev_hash`, which merge mutated — original signatures unverifiable after any merge | HIGH | Decision signature now covers only canonical content (`id` excludes signature itself). Chain position lives in separate signed `chain-link` entries produced by the merger. Original signatures stay valid forever; merge appends new signed structure. |
| Authorization ordering relied on actor clocks — backdating attack vector | HIGH/MED | Every decision records `manifest_version_observed` + `role_assertions_head_observed` at sign time. Authorization checks against the recorded manifest state, not the claimed `ts`. Backdating now requires forging an observation of a past manifest state that contradicts other actors' observations — detectable on every sync. |
| Manifest carried `current_role` per actor AND role-assertions log was the source of truth — divergence risk | MED | Manifest is now a registry only (who's in the team, device keys, admin set, multisig). Current role derives solely from role-assertions.jsonl. Manifest carries `role_assertions_head` pointer to bind the two structures at each manifest version. |
| Merge "dropped" unsignable entries — destroyed evidence | MED | Quarantine schema added. Invalid entries preserved in `quarantine.jsonl` with rejection reason, source git commit, raw entry. Never deleted. |
| MCP session-bound identity = "process with signer access produced this," not "human consciously approved" | MED | Added `signer_consent` field on every decision (`implicit` / `explicit-confirmed` / `reviewed-merge`). Local signer policy enforces consent requirements per decision type. Reviewed-writes and team-wide assertions REQUIRE explicit human confirmation, enforced at the signer layer, not just documented. |
| "Each human is one actor" contradicted by "device keys are separate actor_ids" | MED | Resolved: `human_actor_id` is the stable attribution identity; `device_key_id` is per-device signing material. Manifest binds each human to a set of currently-valid device keys. Decisions attributed to humans, signed by devices. |

The revision changes the schemas and the merge algorithm but does not change the conceptual model: many cryptographically-distinct human actors sharing one team context, with cryptographic identity, role-based authorization, and a verifiable audit chain. The corrections make those promises actually hold rather than just declarative.

**v1.0-rev1** — initial spec, integrating four-layer framing + memory-poisoning/blast-radius/drift defenses from a separate review.

---

## Footnote: why this is the right next architectural milestone

The substrate's existing posture — "no asset without a brief, no slice without real friction, no claim without evidence" — applies to itself. Multi-tenant is not speculative product expansion; it's the architectural answer to "what does the substrate become when it survives more than one operator?"

The honest motivation:

- The "year of attributed decisions" depth conversation only becomes interesting if attribution is verifiable. Descriptive identity falls apart in any context where the operator isn't the only writer.
- The team-collaboration use case is real even at small scale: pair-programming with AI agents, co-maintained OSS projects, contracting work where a contractor's agent decisions need to be auditable by the hiring party.
- v0.x's clean identity ("everything is mau's machine") will eventually become its limitation. The migration to multi-tenant is easier to do at v0.4 than at v0.40 with thousands of installs in the wild.

The substrate stays useful for individuals throughout. The team tier exists for teams that have outgrown "one operator with several agents" and need real cryptographic separation between writers.
