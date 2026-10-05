# Shared mail context — 5 October 2026

Status: **in review**. User-scoped implementation across T06/T08/T10/T11 context
boundaries; no claim that whole backlog packages or live release gates are verified.

Branch: `codex/shared-mail-context`.
Base: `419a2e2aa4720aa1883c7163f8d3953eac2377ae`
(`codex/assistant-intent-routing`, verified against the remote before implementation).
The original dirty `codex/backend-execution-handoff` checkout was preserved.

## Delivered

- Whole provider-thread conversation reads by default, including collapsed messages.
  Explicit single-message and captured-view scopes remain available.
- One reference-only evidence plan across summary/reply/compose, with a separate
  exact reply target and up to four additional supporting references.
- Fair bounded excerpts, deterministic boundary/target sampling, scope-preserving
  coalescing of same-thread search cards and per-thread coverage.
- Eight stable retained source handles with per-turn history links; later reads
  rehydrate Gmail instead of treating old generated answers as evidence.
- Every source is owner/account/fingerprint checked, including after generation
  and in draft edit/preview prefetch. A supporting-thread change blocks publication.
- Fixed async task refresh after conversational draft edits, exposed by the new
  real-database worker/edit/preview regression.
- Runtime architecture/API/data-model docs and versioned release/replay assets.

Design: [shared mail context](../../shared-mail-context.md).
Original replay manifest: [context cases](../../evaluation/shared-mail-context-1.0.json).
Combined release: [integration cases](../../evaluation/shared-mail-context-1.1.json).

## Actual verification

Tests used disposable PostgreSQL 16 in `threadly-shared-context-test`, localhost
port 55441, databases `threadly_test` and `threadly_context_final`, always with
`THREADLY_REQUIRE_TEST_DB=1`. No developer mailbox database was used.
Python: repository's existing backend virtual environment (3.14).

- Full backend `python -m pytest -q --tb=short`: **1,633 passed, zero failed/skipped**.
- After the final supporting-scope adjustment, focused shared-context/conversation/
  batch/compound suite: **133 passed, zero failed/skipped**.
- Final dedicated shared-context suite, including both summary publication paths:
  **20 passed, zero failed/skipped**.
- After assigning the distinct release version, committed-assets and versioned
  Calendar read replay tests: **2 passed**. Calendar read behavior is unchanged.
- Ruff and `git diff --check`: passed.
- Playbook validator, backend handoff validator and task-card consistency: passed.
- Two existing FastAPI/Starlette deprecation warnings appeared in test runs.

Earlier failures were fixed: old tests relied on implicit single-message scope;
release fixtures needed versioned replacements; a policy import shadowed Calendar
metadata; draft edits needed an explicit async refresh before task serialization.
Final incremental checks above cover changes made after the full suite started.

No migration/configuration change or new Google permission is required. Existing
reference snapshots retain their historical excerpt/hash behavior.

## Release and integration limits

The original 1.6.0 commit `bd4e688` was published as draft PR #89. Its exact-head
GitHub CI passed **1,635 tests**, zero failures/skips, plus all offline checks.
Calendar PR #87 subsequently merged at `8a9e72cc049013d5f4d8dc1ac596d2e3ed173fe5`.
That base is now integrated into this isolated branch without rewriting history.
The combined release is `contextual-conversation-1.7.0`; historical 1.5.0/1.6.0
assets and receipts remain unchanged. Both tool sets, retained email handles,
pending Calendar event context, approval settings, receipt filtering and action
status hydration are preserved. The evaluator now names new assets from the
current release rather than overwriting a historical filename.

Integration checks use a new disposable PostgreSQL 16 container,
`threadly-shared-context-integration`, localhost 55441, database
`threadly_context_integration`, with `THREADLY_REQUIRE_TEST_DB=1`.
Focused integration checks: **134 passed**, zero failures/skips. These include
shared context, Calendar creation/permissions, Calendar reads/replays and migrations.
The new cross-feature regression verifies retained email handles survive a Calendar
title clarification and idempotent response replay under both Ask and Always allow.
Full merged backend suite: **1,677 passed**, zero failures/skips, in 487.62 seconds.
Two existing FastAPI/Starlette deprecation warnings remain. Ruff, whitespace,
playbook/handoff validators and generated card consistency checks also passed.
There is no additional migration beyond Calendar's existing `c061026e9041`.
The separate 1.5.1 follow-up about time-before-title booking wording, clarification
guards and pending-field preservation is not part of this integration. When that
follow-up is integrated, reconcile its prompt/schema/runtime changes with this
1.7.0 release, pin fresh combined assets and rerun affected checks. No auto-merge
or rollout is authorized.

Live Bedrock quality evaluation, controlled Gmail/browser acceptance,
deployment and real sends/bookings were **not performed** for shared context. Deterministic tests
establish source delivery and backend boundaries, not semantic model accuracy.
Attachment contents, unlimited history, semantic relevance ranking and autonomous
whole-mailbox discovery remain outside this slice. Existing search tools only use
bounded user-supplied terms. Next gate: review the combined release, evaluate synthetic model
cases and controlled Gmail thread behavior, then separately authorize rollout.
