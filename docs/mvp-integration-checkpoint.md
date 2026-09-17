# Combined MVP backend integration — evidence checkpoint

User scope: remaining bounded MVP backend implementation in **one PR**, then deploy
the reviewed merged revision to EC2. Frontend deferred. No automatic merge, real send,
real invitation or billed model evaluation was performed as part of local development.

Base: merged PR #36 `06ca1a0b45afb50bd20dad9dfb6c02cc66603d0f`.
Branch: `codex/mvp-backend-integration`. Isolated worktree preserves unrelated work.

## Implemented

- Finite durable graphs: singles, slots→draft and summary→slots→draft, whole-request
  preflight, separate artifacts, source/lease/release checks and checkpoint reuse.
- Reviewed whole-command coordinator; no dropping unsupported steps or negations.
- Evidence-backed non-calendar plans, current revisions, explicit dependency-closed
  selection and selected-only drafts; stale grounding blocks outgoing review/actions.
- Historical later-email choice proposals and exact-time fresh checks; explicit fresh
  offer adoption/selection remains a separate user step.
- Exact single-event Calendar preview/approval, fresh ACL/busy checks, stable identity,
  one insert intent and bounded uncertain-outcome reads; separate from Gmail approval.
- Pilot-only Google write reconnect/enablement; empty allowlist/default flags deny writes.
- Scoped entity candidates and current selected commitments; no fabricated global coverage.
- Optional auxiliary native/Flow registry, reproducible prompts/schemas and replay tests.
- Durable paged mailbox staging, history replay and atomic version-fenced publication.
- Owner-only operational counts, worker health, intent switches and conservative cleanup.
- Deployment ordering for all three workers; runtime contracts, diagrams and live gate.

Migrations: `a17026e9a037` workflow/plan/ordinal bounds, immutable input and guarded
rollback; `b17026e9a038` owned sync jobs/staging, active-job uniqueness and lease checks.
Historical task hashes/releases are preserved.

## Verification

Full local regression: **1,042 passed, 0 skipped** in 442.93 seconds. Subsequent
review added standalone context-free compose/scheduling dispatch and stronger selected-
commitment checks; **47 focused tests passed** after those changes. GitHub PR checks
provide the exact-head full-suite result; the PR description records the CI run.
Development failures were investigated: bounded HTTP response handling was corrected;
old disabled-capability fixtures updated for explicit pilot rollout; new migration
fixtures corrected to obey existing event-sequence and quoted-column contracts.
The Calendar crash test uses the actual `Dispatch.request` contract and approval's
202 queued response. These are not live provider tests.

Actual checks already completed:

- 13 offline EC2 deployment/preflight tests passed, including pilot/reconciliation config.
- 19 prototype/summary-console tests passed (offline).
- New auxiliary CloudFormation template passed cfn-lint offline.
- Versioned MVP prompt assets match runtime.
- Ruff, shell syntax, plan validator and backend-handoff/card validator passed.

Test environment: Python 3.12, PostgreSQL 16 disposable database,
`THREADLY_REQUIRE_TEST_DB=1`. Fake Google/model transports, not real provider calls.
See `docs/testing/mvp-release-gate.md` for replay and live acceptance procedure.

## Remaining gates and limits

No current EC2 deployment, live model quality or real Google write claimed.
Native/Flow output contract tests cannot establish semantic entailment. A prior
transient Calendar expiry during setup was not reproduced in later focused/full runs;
continue to enforce expiry rather than extending it silently.

Frontend/client acceptance, live OAuth/revocation, Calendar comparisons, model holdouts,
controlled real send/event recovery, deployed backup restore, sizing and pilot sign-off
remain open. The general BERT command router is not implemented because the supplied
model classifies email content. ADR 004 records the narrower reviewed backend-owned
orchestration instead of the originally proposed Flow read callback/arbitrary planner.
All package statuses remain conservative; no broad task is marked verified from mocks.

Next: exact-head review/CI → user merges PR → pinned EC2 deployment → live acceptance.
