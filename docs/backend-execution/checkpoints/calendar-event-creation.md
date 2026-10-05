# Direct Calendar creation and chat approval — 2026-10-05

## Scope and status

B15 extension for user-requested standalone single events, plus the explicitly
requested Ask / Always allow modes. Backend branch `codex/calendar-event-creation`
starts at `419a2e2aa4720aa1883c7163f8d3953eac2377ae`. Frontend counterpart is
`codex/calendar-permission-controls` (extension 0.2.3). Local implementation and
focused checks complete; CI/production delivery still pending at this checkpoint.
This does not mark the entire historical B15 live-provider acceptance complete.

## Behavior

The new terminal tool freezes a user-origin candidate without an email thread.
The backend resolves timezone/date/default duration, retains missing-title context,
checks writable selected destination, and reuses existing exact approvals, jobs,
preflight ACL/free-busy checks, stable event IDs and uncertain-outcome reconciliation.
Ask is default. Always grants Calendar creation/invitations only in the current
chat, bound to session/account and permission version. It never enables Gmail send,
update/delete/recurring events. Changing the mode, sign-out, chat deletion, and
preference changes fence work before dispatch; dispatched outcomes still reconcile.
Unknown coverage and busy time stop creation. Held preflight errors are visibly
stopped instead of remaining Queued. API, architecture, data and privacy docs updated.

## Evidence

- PostgreSQL 16 disposable instance on local port 64727, Python 3.14 local:
  initial full suite: 1,633 passed / 2 failed (old release fixture references).
  Updated fixture references and preserved historical assets. Affected suite before the final boundary repair:
  **100 passed**, no skips: Calendar creation/actions/conversation tools/migrations.
- New creation tests: exact approval, two-owner access, stale mode versions, missing
  title continuation, missing grant, session/mode/preferences/deletion fences,
  one insert, timeout after acceptance and exact-ID reconciliation, interrupted
  response replay, idempotency conflict, explicit intent, omitted constraints,
  model-supplied authorization rejection and preflight error status.
- Ruff app/tests/migration and `git diff --check`: passed.
- Deployment controls: **30 passed** using project venv. Initial system-Python run
  lacked installed dependencies; rerun with the project runtime passed.
- Plan/handoff validators: passed. No backlog task completion inferred.
- Synthetic live Bedrock: initial 7/9 exposed title follow-up routing and a negative
  case scoring issue. Revised complete replay **9/9**, with all Google access and
  external writes disabled. Reports and final prompt/tool hashes are under
  `docs/evaluation/calendar-event-creation/`. Old live-v1 report retained.
- Frontend validation is recorded in its branch docs; no live event/invitation was
  created during this implementation.

## Second review

Checked model vs UI authority, exact payload/version binding, owner-scoped reads,
transaction rollback/checkpoint replay, User lock revocation order and dispatch
revalidation. Corrected an initial task event sequence to match the existing DB
constraint. Network calls occur outside DB transactions. Candidate/worker tests
assert this with a real database. Existing email and scheduling paths remain intact.

## Rollout

Migration `c061026e9041` after `c33026e9a040` adds three conversation fields and a
check constraint. Old chats default to Ask. Downgrade drops only the new mode
columns; action audit data remains. Runtime flags are `CALENDAR_WRITES_ENABLED`,
`CALENDAR_RECONCILIATION_ENABLED`, and `WRITE_PILOT_USER_IDS`; keep Gmail writes
unchanged. Production preflight confirmed the requesting owner (ID 1) is connected,
has Calendar preferences, and has not granted `calendar.events` yet. Enable only
this owner for Calendar rollout, then complete Google's event-creation consent.
No real-calendar success is claimed until Google confirms a user-approved event.


Final review also found that a creation verb on a later line could be quoted email
under a summarisation request. Creation now must lead the user's request; creating
a summary/draft/message is not Calendar authority. Three real-DB regressions force
a mistaken model tool call under Always mode and require zero actions or jobs.
Final boundary repair: **24/24** direct-event PostgreSQL tests passed, including
all three pasted-instruction cases. Prompt/tool assets are unchanged by this
backend-only guard correction. Exact updated commit CI must pass before release.
