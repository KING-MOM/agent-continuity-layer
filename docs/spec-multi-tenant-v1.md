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
- `human_actor_id` = fingerprint of the human's stable identity (the attribution identity used for roles, audit, and team membership). Stable as long as the human is in the team.
- `device_key_id` = fingerprint of one of the human's per-device Ed25519 public keys. A human may have multiple device keys active simultaneously (laptop, desktop, etc.); each device's private key never leaves that device.
- `pubkey` = the Ed25519 public key behind a `device_key_id`. Distributed via the team's signed manifest, which binds each `human_actor_id` to a set of currently-valid `device_key_id`s.
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

### 3. Audit integrity is a hash chain via chain-link entries

Decision content is signed and immutable. Chain position is established by separate **chain-link** entries that each cite the previous chain-link's hash. The chain is the sequence of chain-links, not a property of the decisions themselves.

To insert a fabricated decision retroactively, an attacker would have to:
- Sign the decision (requires a currently-valid device key bound in the manifest)
- Create a chain-link placing it in the canonical order (requires the merger's device key, since merge-links are merger-signed)
- Produce a new audit-anchor naming that chain head (also merger-signed)
- Convince every other actor's verifier that this anchor is canonical (it's not — every other actor has anchors from prior merges that contradict it)

The hash chain is **per-team, not per-actor**. Multiple actors' decisions all interleave into one chain-link sequence. The chain's order is determined by sync semantics (see Decision 4) and named explicitly by the active audit-anchor (see "Chain epoch and supersession" below).

Why hash chain and not merkle tree:
- Hash chain is simpler. Merkle gives you efficient inclusion proofs (useful for selective disclosure: "here's proof this one decision is in the log without revealing the rest"). For v1.0, that use case isn't there yet. Linear hash chain is sufficient.
- Merkle remains a v1.x option if selective-disclosure becomes a real need (e.g. compliance audit where you want to prove "decision X was made on date Y" without exposing decisions A through W).

**Audit anchoring** is integral, not optional. Every successful sync produces a new signed audit-anchor naming the chain head + chain epoch + observed git commit. The latest valid anchor is what defines the canonical chain. Self-anchoring (free) gives team-internal non-repudiation; third-party notarization (paid tier) adds external time attestation.

### 4. Multi-writer sync is convergent via epoch supersession

The hard case: two actors both append decisions before sync. Each, working locally, creates chain-link entries with `chain_position: 1, 2, ...` and an audit-anchor citing their local chain head. Both views are internally consistent. Neither is wrong. The substrate's job is to produce a single canonical merged order without invalidating either actor's prior work.

The algorithm uses **chain epochs**. Every chain-link and audit-anchor carries a `chain_epoch` integer. Within an epoch, chain-link positions are strictly monotonic and `prev_chain_link_hash` references form a linear chain. Across epochs, an explicit supersession relationship is recorded.

Concurrent writers each operate in the same epoch they last observed (call it epoch N). When sync detects divergence, the merger:

1. Reads all chain-link entries from local + remote at epoch N.
2. Validates every decision and chain-link signature against the manifest active at each entry's `manifest_version_observed`. Any failures go to quarantine (see Decision 4 below).
3. Interleaves valid decisions by `(ts, human_actor_id, device_key_id)` lexicographic tiebreak. Deterministic.
4. Appends a fresh sequence of chain-link entries at **epoch N+1**, each carrying:
   - `chain_epoch: N+1`
   - `chain_position` numbered from 1 within the new epoch
   - `prev_chain_link_hash` linking to the previous epoch-N+1 link
   - `is_merge_link: true` on the first epoch-N+1 link
   - `supersedes_epoch: N` on the first link of the new epoch
   - `source_actors` listing the human_actor_ids whose decisions are being merged
5. Signs each new chain-link with the merger's device key.
6. Publishes a new audit-anchor for epoch N+1 naming the new chain head.

**Old chain-links from epoch N remain in `chain-links.jsonl`.** They are preserved as evidence — they testify that each actor genuinely had a coherent local view before the merge. The canonical chain is whatever the latest audit-anchor names, NOT whatever has the highest chain_position number. A verifier walks back from the latest audit-anchor through its epoch's chain-links; lower-epoch links are reachable for forensics but not authoritative.

What this means in practice:
- A decision can have only one signature (from its author at write time) but can appear in multiple chain-links across epochs. The decision's content_id is stable; its chain-position within the *active* epoch is whatever the latest anchor says.
- An actor who tries to roll back an anchor (publish an audit-anchor naming a stale epoch as active) is detectable: other actors hold anchors at later epochs with strictly higher chain_epoch values; signatures on those later anchors are valid; the rollback attempt fails verification because the manifest's `latest_anchor_epoch` pointer (updated on every legitimate anchor publication) records the team-known maximum.
- Pre-merge chain-links are not "wrong" — they were correct given the writer's local view at the time. They become non-canonical when superseded, not invalid.

The `id` of each decision is `sha256(canonical body excluding device_signature)`. That stays stable forever — across merges, across epochs, across team transfers. Decisions are content-addressed; chain-links are epoch-positioned; audit-anchors are the authoritative pointer.

### 5. Single-tenant installs become "team of one"

Existing v0.x installs migrate to v1.0 by being treated as a one-actor team:
- A `team_id` is generated locally (`personal-<machine-id>` or operator-supplied).
- A `human_actor_id` is derived from a locally-generated Ed25519 keypair; the operator can attach an optional `display_name`.
- A `device_key_id` for this machine is registered in the manifest under the human_actor.
- All existing decisions are rewritten in-place to v2.0 schema: `team_id`, `human_actor_id`, `device_key_id`, `manifest_version_observed: 1`, `role_assertions_head_observed: <initial>`, `signer_consent: implicit`, and a `device_signature` over the canonical body. A marker field `migrated_from_v0: true` flags these as migration-era signatures (the signature attests to migration provenance, not original authoring intent at decision time).
- Chain-link entries are generated for every decision in `ts` order, all at `chain_epoch: 1`, signed by the migration tool's device key (which is the operator's only device at migration time).
- An initial audit-anchor is published naming chain epoch 1's head.

This is a one-time migration on first run of v1.0. Idempotent: re-running is a no-op. Reversible: a `migrate --rollback` keeps the v0.x format archived.

The operational impact for an individual operator: their decisions log gains the v2.0 fields (`team_id`, `human_actor_id`, `device_key_id`, `manifest_version_observed`, `role_assertions_head_observed`, `signer_consent`, `device_signature`), gains a new `chain-links.jsonl` file alongside `decisions.jsonl`, and gains `team-manifest.json` + `role-assertions.jsonl` + `audit-anchors.jsonl`. The CLI surface is unchanged — `decisions add`, `read_decisions`, MCP behavior all keep their semantics, with cryptographic integrity now backing them.

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
  "chain_epoch": "<integer, monotonic per team; increments on every merge>",
  "chain_position": "<integer, monotonic within this epoch (resets per epoch)>",
  "prev_chain_link_hash": "<sha256 of previous chain-link body in this epoch, or zero for epoch genesis>",
  "supersedes_epoch": "<integer, nullable — set only on the first chain-link of a new epoch produced by a merge, names the epoch being superseded>",
  "linked_by_human_actor_id": "<who placed this in the chain>",
  "linked_by_device_key_id": "<which device signed the link>",
  "linked_at": "<ISO-8601 UTC>",
  "merge_context": {
    "is_merge_link": "<bool>",
    "source_actors": ["<human_actor_id>", "..."],
    "git_commit_observed": "<sha of git-memory repo HEAD at link time>"
  },
  "link_signature": "<base64 Ed25519 of canonical body by linker's device_key>"
}
```

Chain-links live in `<memory-repo>/chain-links.jsonl`. The active chain is verified by:
1. Reading the latest valid audit-anchor (highest `chain_epoch` with verifiable signature against current manifest).
2. Walking back from the anchor's named chain head through chain-links at that epoch only, validating each link's `prev_chain_link_hash` and `link_signature`.
3. Lower-epoch chain-links remain readable for forensics but are NOT part of the canonical chain. They testify to pre-merge views of the chain; the canonical chain is whatever the latest audit-anchor names.

Key property: **a decision's content signature is invariant once written.** Only its chain-link epoch and position are subject to merge rewriting. The original decision-author's signature stays valid forever; merges produce NEW chain-link signatures by the merger over a NEW canonical ordering in a NEW epoch. Both are verifiable independently. The previous epoch's chain-links are preserved as historical evidence — they were correct given the writer's local view at the time, and they become non-canonical (not invalid) when superseded.

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
  "latest_anchor_epoch_observed": "<integer, highest chain_epoch the manifest signers have witnessed at this manifest_version>",
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

### NEW: audit-anchor.schema.json (v1.0)

Audit-anchors are integral, not optional. The active chain is defined by the latest valid audit-anchor: it names the current `chain_epoch` and the chain head within that epoch.

```json
{
  "schema_version": "1.0",
  "type": "audit-anchor",
  "team_id": "<uuid>",
  "chain_epoch": "<integer, names which epoch is being anchored>",
  "chain_head_link_id": "<sha256 of the chain-link entry at this epoch's tail>",
  "chain_head_link_position": "<integer, the chain_position of the tail link within this epoch>",
  "supersedes_epoch": "<integer, nullable — the epoch this anchor replaces as canonical>",
  "anchored_at": "<ISO-8601 UTC>",
  "anchored_by_human_actor_id": "<who produced this anchor>",
  "anchored_by_device_key_id": "<which device signed>",
  "anchor_source": "<self|notary-service-url>",
  "notary_attestation": "<base64, nullable — present only when anchor_source != self>",
  "anchor_signature": "<base64 Ed25519 over canonical body by the anchoring device>"
}
```

Audit-anchors live in `<memory-repo>/audit-anchors.jsonl`. Self-anchored by team admins (or by the merger at sync completion) is free. Third-party-notarized adds a `notary_attestation` and is the paid-tier service.

**The active anchor is the one with the highest `chain_epoch` that verifies against the current manifest.** A rollback attempt — publishing an anchor with a lower `chain_epoch` than the team-known maximum — fails verification because the team-manifest's `latest_anchor_epoch_observed` field (updated by every legitimate anchor publication) records the maximum seen. An anchor whose `chain_epoch` ≤ `latest_anchor_epoch_observed` is rejected as a stale-rollback attempt; the canonical chain stays at the higher epoch.

---

## MCP surface impact

All six adapter-contract operations gain actor context. Two options for how the caller asserts identity:

**Option A: per-call actor signature.** Every `tools/call` includes a `caller_actor_id` + `caller_signature` over the request. Heavy on the wire, strong on integrity.

**Option B: session-bound actor.** `initialize` includes the caller's `human_actor_id` + `device_key_id`; subsequent calls inherit them; the server validates writes against the session's bound identity. Lighter, requires session integrity (stdio MCP is already process-bound, so this is fine).

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

5. **Interleave decisions** by `ts` with `(human_actor_id, device_key_id)` lexicographic tiebreak. The decision content is untouched; only the chain-link ordering and epoch are computed.

6. **Append new chain-links at epoch N+1** for the full merged ordering. Each new chain-link is signed by the merger's device key with `chain_epoch: N+1`, `chain_position` numbered from 1, `prev_chain_link_hash` linking within the new epoch, `is_merge_link: true`, `source_actors` listing every distinct human_actor whose decisions are being merged, and `supersedes_epoch: N` on the first epoch-N+1 link. The merger's chain-link signature is "I observed these decisions in this canonical order at this manifest state" — distinct from the original decision authors' content signatures.

7. **Sign merge audit-anchor at epoch N+1**: append a signed `audit-anchor` entry naming the new chain head, `supersedes_epoch: N`, and the git commit observed at merge time. This anchor is what makes epoch N+1 canonical.

8. **Update team manifest's `latest_anchor_epoch_observed`**: bump manifest_version with the new epoch number. This is the manifest-level rollback defense.

9. **Push**: standard.

If two actors merge concurrently (both producing epoch N+1 anchors from epoch N), the algorithm applies recursively. The second merger sees the first's epoch-N+1 anchor + chain-links during pull, validates them, and produces an epoch-N+2 merge by treating epoch-N+1 as the new base. Epoch numbers always increase; the merge tree always converges. Determinism comes from `(ts, human_actor_id, device_key_id)` tiebreaks within each merge, not from coordination between mergers.

**What stays auditable**: every original decision signature remains valid forever. Any verifier can re-fetch the decisions, validate signatures against the manifest at each decision's `manifest_version_observed`, and confirm the canonical chain by following the latest valid audit-anchor backward through its epoch's chain-links. Lower-epoch chain-links are preserved as evidence of pre-merge views; they were correct at the time and remain readable for forensics, but they aren't canonical once superseded.

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

**v1.0-rev3 (this version)** — second security review pass addressing one new finding plus stale-prose cleanup:

| Finding | Severity | Fix |
|---|---|---|
| Chain-link supersession / active-chain semantics underspecified — concurrent writers could each create valid `chain_position: 1` links and the spec didn't define which was canonical post-merge | MED | Added `chain_epoch` to chain-link and audit-anchor schemas. The active chain is the one named by the latest valid audit-anchor (highest `chain_epoch`). Merges produce chain-links at epoch N+1 with explicit `supersedes_epoch: N`. Old epochs' chain-links remain as evidence. Rollback defense: manifest carries `latest_anchor_epoch_observed`; anchors at lower epochs are rejected. |
| Stale "5 hardest decisions" prose (decisions 3, 4, 5) and migration text still described rev1 model (single `actor_id`, `prev_hash` in decision entry, "id formula excludes prev_hash") after rev2 corrected the schemas section only | doc | Rewrote decisions 3-5 to describe the rev2+ model accurately. Updated migration narrative to enumerate the actual v2.0 fields (`team_id`, `human_actor_id`, `device_key_id`, `manifest_version_observed`, `role_assertions_head_observed`, `signer_consent`, `device_signature`) and the migration-era marker. |

The rev3 changes are entirely within the cryptographic + sync model — no new architectural concepts, no policy changes. The conceptual contract (many cryptographically-distinct human actors sharing one team context with verifiable audit) is unchanged; epoch supersession is what makes that contract actually hold under concurrent writes.

**v1.0-rev2** — first security review pass addressing six findings from an independent reviewer:

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
