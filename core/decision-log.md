# Decision log format

`decisions.md` is append-only. One decision per line. Format:

```
[ISO-8601-Z] [project-name] [source-adapter:actor] [task-id-or-NONE] Decision summary. Reason: ... Rejected: ...
```

Example:
```
[2026-05-23T18:00:00Z] [Blue Sky] [openclaw:mika] [NONE] Use Respond.io webhook instead of polling. Reason: Lower latency, no rate limit concerns. Rejected: Pipedream scheduled trigger (too slow).
[2026-05-23T18:42:00Z] [Blue Sky] [claude:worker] [task-abc123def456] Adopt zod for inbound webhook validation. Reason: Already a dep; matches existing API surface. Rejected: io-ts (not in tree), hand-rolled types (no runtime check).
```

## Rules

- **Never edit past entries.** Corrections go in as new entries that reference the old line: `Correction to 2026-05-23T18:00:00Z: ...`
- **Decisions ≠ status updates.** A decision is a fork in the road that was resolved. "Started X" is not a decision. "Chose X over Y because Z" is.
- **One line per decision.** Hard wrap is fine in source but the parser splits on newlines, so don't break a single decision across lines.
- **Anchor the project name** even though the file is already scoped to one project — makes cross-project audit trivial.
