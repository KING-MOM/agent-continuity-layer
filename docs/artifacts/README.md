# Milestone evidence artifacts

Concrete evidence captured during milestone validation runs that should outlive `~/.cache/agent-continuity/queue/`.

## When to add to this tree

Before any cleanup of `~/.cache/agent-continuity/queue/` that would destroy task records the reviewer (or future you) might need to refer back to, export the relevant task JSON here first:

```bash
mkdir -p docs/artifacts/{milestone}/
cp ~/.cache/agent-continuity/queue/{state}/{task-id}.json docs/artifacts/{milestone}/
```

Then clean.

## Layout

```
docs/artifacts/
  {milestone}/                      e.g. m4-4/, m4-5/
    {task-id}.json                  full task record: audit transitions + result.artifacts inline
    notes.md                        optional operator narrative
```

`{task-id}.json` files are checked in verbatim — they include the embedded patch / report content, so they're the canonical "what actually happened" record once the queue is cleaned.

## Smoke tests vs milestone evidence

For ad-hoc smoke tests on top of existing milestone artifacts, prefer surgical cleanup:

```bash
rm ~/.cache/agent-continuity/queue/{state}/{specific-test-task-id}.json
```

rather than `rm -rf` of the whole tree. Broad wipes silently destroy evidence the reviewer hasn't inspected yet — see `feedback_no_unilateral_queue_cleanup.md` in the operator's memory store for the precedent.

## Provenance

Established 2026-05-25 after the M4.6 commit (`b5f76f4`) accidentally wiped the M4.4 + M4.5 in-queue evidence (`task-8d267ed68469`, `task-9ce4efd925e5`). The substantive narrative for those two tasks now lives only in commit messages `2abb429` and `51519d2`, plus the still-uncommitted edit at `~/.openclaw/workspace/m4-fixture/fixtures/sample.md` (the actual patch M4.4 produced).

From M5 onward: export first, clean second.
