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
Replay manifest: [context cases](../../evaluation/shared-mail-context-1.0.json).

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

The distinct context release is `contextual-conversation-1.6.0`; context policy and
prompt hashes are saved with each plan. Historical release files remain unchanged.
The concurrently developed Calendar change uses 1.5.0. This branch does **not**
include that unmerged change. Integration must reconcile overlapping conversation
runtime/schema/prompt files, preserve both tool sets, regenerate the combined
release assets, and rerun affected checks before merge. No auto-merge is authorized.

Live Bedrock quality evaluation, controlled Gmail/browser acceptance, publishing,
deployment and real sends/bookings were **not performed**. Deterministic tests
establish source delivery and backend boundaries, not semantic model accuracy.
Attachment contents, unlimited history, semantic relevance ranking and autonomous
whole-mailbox discovery remain outside this slice. Existing search tools only use
bounded user-supplied terms. Next gate: integrate/review, evaluate synthetic model
cases and controlled Gmail thread behavior, then separately authorize rollout.
