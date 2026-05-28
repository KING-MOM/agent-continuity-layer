# core/migrations/

Per-version-pair schema migration scripts for the agent-continuity
substrate. The M12.3 runner (`scripts/_migrate.py`, invoked via
`scripts/migrate.sh` or `agent-continuity migrate`) discovers files
in this directory and walks the user from their current version to
the target version one hop at a time.

## File naming

```
v{FROM}_to_v{TO}.py
```

Examples:

- `v0.1.0_to_v0.1.1.py`
- `v0.1.1_to_v0.2.0.py`

The runner chains them: if available migrations cover the edges
`0.1.0 → 0.1.1` and `0.1.1 → 0.2.0`, then `--from 0.1.0 --to 0.2.0`
runs both, in order. If no chain exists, the runner refuses (apply
never runs without a valid plan).

## Module contract

Each file must define:

```python
FROM_VERSION = "0.1.0"
TO_VERSION   = "0.1.1"

def affected_paths(env: dict) -> list:
    """Return paths this migration may read or write.

    env is a dict of resolved XDG-base paths:
      env["config"]    — $XDG_CONFIG_HOME/agent-continuity
      env["state"]     — $XDG_STATE_HOME/agent-continuity
      env["cache"]     — $XDG_CACHE_HOME/agent-continuity
      env["data"]      — $XDG_DATA_HOME/agent-continuity
      env["substrate"] — the installed substrate dir

    affected_paths() must be PURE: read-only, deterministic, no I/O
    beyond stat/exists checks. The runner calls it during dry-run
    to print the plan.
    """
    return []

def migrate(env: dict, dry_run: bool = False) -> None:
    """Apply the migration.

    The runner calls this with dry_run=False during apply. When
    dry_run is True, do not write — only inspect and return. The
    runner already prevents apply when a plan is invalid, so
    migrate() itself does not need to re-check the chain.
    """
    pass
```

## Invariants

- M12.3 is **framework-only**: no real migrations ship in this
  slice. `agent-continuity migrate` and `migrate --dry-run` are
  both no-ops on a fresh install.
- The runner **never** runs automatically — not from install.sh,
  not from doctor, not from any other script. It must be invoked
  explicitly by the operator.
- `migrate` (apply) refuses to proceed unless the plan phase
  succeeds. A failed `affected_paths()` call counts as a planning
  failure.
- The substrate **does not yet write a marker file** describing
  the user's installed-data version. Until that lands, `--from`
  defaults to the substrate's own `core/VERSION`, which means
  `from == to` and no migrations apply by default.
