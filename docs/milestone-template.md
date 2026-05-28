# Milestone Proposal Template

Use this before starting any new milestone. The goal is to prevent infrastructure drift and keep the project centered on continuity.

## Milestone

Name:

Status:

Owner / operator:

## Continuity Primitive Strengthened

Choose at least one:

- Project registry
- Context recovery
- Decision log
- History
- Trust policy
- Handoff ledger
- Artifact memory
- Adapter portability

## User Story

As an agent or operator, I need...

So that...

## What A New Agent Knows After This

After this milestone, a fresh agent entering the project can know...

## Handoff Artifact Produced

What durable artifact survives the session?

Examples: context update, decision entry, task record, patch, report, test result, sync metadata, artifact bundle.

## Trust / Approval Impact

What authority changes?

What still requires human approval?

What is explicitly refused?

## Adapter Impact

Which adapters are affected?

- OpenClaw
- Claude local
- Codex local
- Web agent
- VM sync
- CLI only
- Other

## Out Of Scope

What are we deliberately not doing?

## Acceptance Tests

- [ ] Continuity primitive is visibly stronger.
- [ ] Durable artifact exists after the session ends.
- [ ] Trust boundary is unchanged or explicitly documented.
- [ ] Doctor or docs can surface the new behavior.
- [ ] Existing handoff/delegation paths still work if touched.
