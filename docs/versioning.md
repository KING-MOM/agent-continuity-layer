# Versioning Policy

The substrate follows [Semantic Versioning 2.0.0](https://semver.org/). This document defines exactly what the version number commits to — which parts of the surface are stable and which are not — and what the criteria are for reaching `v1.0.0`.

## Current status

`v0.x.x`. The project is **pre-stable**. Breaking changes can land in any minor version (`v0.1.x → v0.2.x`) within the pre-1.0 era. Patch versions (`v0.1.5 → v0.1.6`) remain additive and non-breaking.

This is the standard SemVer interpretation of the pre-1.0 era: the project signals that the API surface is still being shaped, and consumers should pin to specific minors rather than treating `^0.1` as a compatibility range.

## What "the API surface" means

Three layers, each with its own stability commitment.

### 1. The six-operation adapter contract (`docs/m9-adapter-pattern.md`)

The operations themselves — `whoami`, `read_context`, `read_decisions`, `append_decision`, `claim_task`, `submit_result` — are the load-bearing public interface.

- **Pre-1.0:** signatures, argument shapes, and response shapes may change between minors. Each minor that changes them documents the change in the release notes.
- **Post-1.0:** adding new optional fields is non-breaking (minor bump). Removing fields, renaming operations, or changing semantics is breaking (major bump). New operations are minor bumps.

### 2. The CLI dispatcher surface (`agent-continuity ...`)

The subcommands exposed via `bin/agent-continuity` — `doctor`, `connect`, `quickstart`, `decisions`, `context`, `project`, `worker`, `bundle`, `mcp`, `reference-agent`, `sync`, `migrate`.

- **Pre-1.0:** subcommand names, flag names, and exit codes may change between minors. We document changes in release notes.
- **Post-1.0:** existing subcommand+flag combinations preserve their exit-code semantics and primary output shape. New subcommands and new flags are minor bumps. Removing or renaming is a major bump.
- Stdout content remains the contract; stderr is best-effort diagnostic and can change freely.

### 3. The file schemas (`core/schemas/*.schema.json`)

The schemas the substrate reads and writes on disk: `decision-entry`, `project-registry-entry`, `adapter-identity`, `adapter-bundle`, `worker-task`, `device-identity`, `context-pinned`, `sync-metadata`, `trust-policy`, and the MCP tools manifest.

- **Pre-1.0:** schema `schema_version` may bump between minors. Migrations between adjacent schema versions ship in `core/migrations/v{X}_to_v{Y}.py` per the M12.3 contract.
- **Post-1.0:** schema bumps only land in major releases. The substrate maintains read compatibility for the previous major version's schemas (so a v1.x install can read v1.0 data without forced migration).

### What's NOT part of the API surface

- Internal Python module organization (`scripts/_doctor.py` etc.) — may refactor freely.
- Bash script implementation details — wrappers around the Python modules.
- Trust-policy enforcement order (the *contract* is "enforce trust on these operations"; the order is implementation).
- Doctor output formatting — JSON shape is stable for tooling, human format may change.
- Bundle internals — the bundle schema is stable; the workspace state inside a bundle can vary.
- Test fixtures, smoke harnesses, and CI configuration.

## Backward compatibility per release class

| Release class | Adapter contract | CLI surface | Schemas | Migrations required? |
|---|---|---|---|---|
| Patch (`v0.1.5 → v0.1.6`) | No change | No change | No change | No |
| Minor pre-1.0 (`v0.1 → v0.2`) | May change | May change | May bump | Yes, runner ships them |
| Minor post-1.0 (`v1.0 → v1.1`) | Additive only | Additive only | No bump | No |
| Major (`v1.x → v2.0`) | May change | May change | May bump | Yes, runner ships them |

## Deprecation policy

When a feature is deprecated:

1. The deprecation lands in a minor release with a `[deprecated]` marker in the CLI help / doc.
2. A `WARN`-level deprecation message appears whenever the deprecated feature is invoked.
3. The feature continues to work for at least one more minor cycle before removal.
4. Removal lands in the next major version (post-1.0) or the next minor (pre-1.0).

This applies to: CLI flags, subcommand names, schema fields with `"deprecated": true`, and any operation marked deprecated in `m9-adapter-pattern.md`.

## Criteria for reaching v1.0.0

`v1.0` is not on a calendar timeline. It ships when **all** of the following are true:

1. **Schemas frozen for ≥ 3 months.** No schema bumps in the recent release window.
2. **Adapter contract stable.** No changes to the six operations' signatures for ≥ 3 months.
3. **Signed releases shipping** (M15.3) and signature verification documented as the canonical install path.
4. **Reproducible builds** (M15.1) verified by at least one third party.
5. **SBOM** (M15.2) published per release.
6. **SECURITY.md** in place (M15.0 — this slice).
7. **A documented threat model** (`docs/threat-model.md`) covering the v1.0 surface.
8. **At least one external user in production**, willing to be referenced by name if asked.
9. **Bus factor ≥ 2** OR a documented succession plan if the primary maintainer is unavailable.
10. **No known unfixed security issues of medium severity or above.**

Items 1–7 are technical and within the maintainer's control. Items 8–10 require external signal that the project has earned the stability `v1.0` implies.

We will not bump to `v1.0` to chase enterprise pitch signal if the criteria above aren't met. Versioning is a trust mechanism; padding the major version against the substance erodes that trust.

## LTS policy

Currently: **no LTS for v0.x.** Only the latest minor (`v0.1.x`) receives security fixes. Users on older minors are expected to upgrade.

When `v1.0` ships, the LTS policy will be defined at that time. The reasonable shape (subject to operator capacity at that point):

- The latest minor of each major receives feature + security fixes for 12 months after the next major ships.
- Older minors receive security-only fixes for 6 additional months, then are end-of-life.

This shape is **not a commitment** today — documenting the intended structure so future readers know what direction LTS will likely take.

## How we announce changes

- **Release notes** in each GitHub release: every breaking change, deprecation, and migration is named explicitly with examples.
- **CHANGELOG**: not currently maintained as a separate file; the release notes serve as the changelog. This may change post-1.0.
- **Migration scripts** (`core/migrations/v{X}_to_v{Y}.py`): for every schema bump, an idempotent migration script lands in the same release.

## Pinning recommendations

For consumers depending on this substrate:

- **In a script / CI / install command:** pin to a specific version (`v0.1.6`), not `latest`. The `latest` URL is convenient for adoption demos but is not appropriate for reproducible deployments.
- **In code that imports the Python SDK:** check `agent-continuity --version` matches expectations at startup, fail loud if not.
- **In documentation:** reference both the minimum supported version and the version the docs were written against.

## What changes in this policy require what

Versioning policy itself follows the same rules: changes that reduce the project's stability commitments require a major bump (e.g., adding a "we may now break the six-op contract within minors" clause would require v2.0). Changes that strengthen commitments (e.g., tightening the deprecation window) ship in any minor.
