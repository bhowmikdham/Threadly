# Implementation checkpoint template

Copy to `docs/backend-execution/checkpoints/Bxx.md` when implementing that task.
This template is not a completion record. Keep it concise and grounded in observed
results; use additional linked reports for large evaluation output.

## Identity

- B task / mapped T packages:
- Branch and verified base commit:
- Implementation commit(s) / PR:
- Current status: planned | in_progress | in_review | verified | blocked
- Local implementation complete: yes/no
- Live integration required: yes/no; result/pending dependency:
- Deployed/enabled: no unless independently verified

## Behavior and decisions

- Concrete behavior delivered:
- Files/contracts changed:
- Invariants preserved:
- Product/architecture decisions and rationale:
- Out-of-scope work still outstanding:

## Acceptance evidence

| Card assertion ID | Test or inspection | Actual result | Evidence location |
|---|---|---|---|
| Bxx-A1 | Fill from task card | pass/fail/pending | test path/report |

Record exact commands, environment and pass/fail/skip counts. Include appropriate
Ruff, PostgreSQL, migration, model/Flow and live-provider results separately.
Do not copy “passed” from a prior run or claim a command you did not run.

## Review

- Ownership / authorization findings and fixes:
- Transaction / concurrency / recovery findings and fixes:
- Contract / migration / compatibility findings and fixes:
- Privacy / source integrity / outcome wording findings and fixes:
- Outstanding material risks, with concrete affected path:
- CI result and reviewed commit:

## Migration and rollout

- Alembic current head, new revision and data preservation:
- Downgrade/rollback behavior:
- Required settings/scopes/roles/feature flags (names only, no secrets):
- Startup/shutdown sequencing:
- Exact remaining authorized test/deployment step:

## Resume

- Next eligible B task and prerequisites satisfied:
- Outstanding external gates and owner/action needed:
- Working tree changes not yet committed:
- Failing test/error and next command if interrupted:
- Original backlog evidence/status updated:

If a task requires live credentials that are unavailable, finish the local adapter,
fixtures and documentation and report the live gate as pending. Do not mark that
acceptance assertion passed or repeatedly ask for the same missing access.
