# Quickstart Fixture Project

This is the fixture repo that `scripts/quickstart.sh init` copies into a sandbox workspace. It exists so a new user can run the continuity layer's full delegated-task loop without pointing it at one of their real projects.

## What lives here

- `README.md` — this file
- `src/example.py` — a placeholder source module
- `tests/test_example.py` — a placeholder test

Nothing in this directory is load-bearing. The fixture is a *museum diorama*: real enough to prove the system, isolated enough that nobody worries it touched their house. The fake worker (M11.1) will reference this project's path in its task input but doesn't actually modify any of these files.

## When you outgrow it

When you're ready to point the continuity layer at one of your real projects, follow [`docs/walkthroughs/codex-local-shell.md`](../../docs/walkthroughs/codex-local-shell.md) using your real repo. The quickstart fixture stays here for the next visitor.
