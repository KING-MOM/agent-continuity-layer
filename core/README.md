# core/

The **contract** every adapter must honor. Nothing in `core/` executes — these are schemas and format specs.

If you change something here, every adapter has to follow. Treat changes like a protocol bump: write a migration in `scripts/migrate.sh` and bump the schema `$id` version.

## Files

- [`schemas/project-registry.schema.json`](schemas/project-registry.schema.json) — the canonical project registry (replaces v0.1's `~/life-agents/sessions/registry.json`)
- [`schemas/worker-task.schema.json`](schemas/worker-task.schema.json) — bounded work units that OpenClaw delegates to Claude/Codex
- [`schemas/trust-policy.schema.json`](schemas/trust-policy.schema.json) — per-repo / per-task-type allow + deny lists
- [`context-format.md`](context-format.md) — spec for the `context.md` / `decisions.md` / `history.md` triad
- [`decision-log.md`](decision-log.md) — how decisions get extracted and appended

## Versioning

Every schema carries a `$id` that ends in `/v1/...`. Bumping the major version is a breaking change and requires:

1. Writing the new schema as `/v2/...`
2. Adding a migrator in `scripts/migrate.sh`
3. Each adapter declares which versions it supports
4. Update `docs/architecture.md` with the rationale
