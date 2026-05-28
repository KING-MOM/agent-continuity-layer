# Contributing

Thanks for taking the project seriously enough to improve it. This repo is a continuity substrate, not a general agent runner, so the fastest way to get a good PR accepted is to anchor the change in a continuity primitive.

## Scope Gate

Before opening a non-trivial PR, answer these four questions in the PR body:

1. Which charter primitive does this strengthen? Context recovery, decision log, trust policy, handoff ledger, artifact memory, adapter portability, project registry, or history/sync.
2. What new memory, audit trail, or trust boundary survives after the session ends?
3. What adapter paths are affected? Shell, MCP, bundle, OpenClaw/Mika, Python SDK, or none.
4. What is explicitly out of scope?

If the change is mostly about subprocess management, prompt construction, or autonomous execution, it probably belongs in an adapter project unless it strengthens one of the primitives above.

## Local Checks

Run these before a PR when relevant:

```bash
scripts/doctor.sh --human
python3 scripts/_sdk_smoke.py
python3 scripts/_reference_agent_smoke.py
scripts/release-smoke.sh
```

For docs-only changes, `scripts/doctor.sh --human` is usually enough. For packaging or command-surface changes, run `scripts/release-smoke.sh` too.

## Release Gate

Release artifacts are built only from a clean tree:

```bash
git status --short
scripts/release.sh build
```

`release.sh` refuses dirty trees except generated files under `dist/`. If a smoke test leaves a timestamped log under `docs/artifacts/`, either commit it as intentional evidence or delete it before building a release.

## Trust And Safety Rules

- Do not broaden write authority silently.
- Do not sync executable behavior, trust policy, skills, or adapter config from the VM.
- Do not make identity sound cryptographic unless it is actually signed or attested.
- Preserve append-only logs. Corrections should be new entries, not edits in place.
- Keep operator-mediated actions explicit. No auto-sync, auto-migrate, or auto-upgrade.

## Documentation Expectations

User-facing behavior needs a doc link. Good targets:

- `README.md` for public entry points.
- `docs/north-star.md` for direction.
- `docs/trust-policy.md` for authorization and local policy behavior.
- `docs/m9-adapter-pattern.md` for adapter-contract changes.
- `docs/roadmap.md` for milestone sequencing.

## Historical Files

`v0.1-reference/` is intentional provenance. Do not remove it just because it is old or legacy-specific; it documents where this substrate came from. If it creates confusion, improve the framing around it instead.
