# Implementation progress

## Bedrock adapter — T01/T04 partial

Branch: `codex/bedrock-provider`. Status: implemented, awaiting review and live AWS
validation. No complete backlog task is marked verified.

Added explicit Bedrock/legacy selection, text-only Converse inference, bounded
SDK calls off the event loop, cloud input masking and sanitized failure handling.
Existing summary SSE consumes validated Bedrock text as one buffered chunk.
Long model identifiers fit the existing provenance column using a stable digest.
ADR 003 supersedes the old inference placement decision.

Verification on Python 3.12.14 (13 September 2026): `ruff check app tests` passed;
`python -m pytest -q` passed 34 tests and skipped 7 PostgreSQL tests because no test
database was reachable. Added 18 synthetic adapter/configuration tests, including
failure, truncation, unexpected tool content, cancellation cleanup and model
selection. These are not live model-quality results. Test dependencies were
installed in a disposable virtual environment; Chroma was not needed or installed.

Configuration: see `.env.example` and ADR 003. Bedrock stays opt-in; no real AWS
model, IAM role or Flow has been configured or invoked. No production deployment.

Next: typed intent proposal routing, then durable request/job/context storage and
the summary vertical slice. Existing summary cache remains keyed by message ID;
the task-based replacement must add model/prompt/context-aware invalidation.

## Intent preview — T01/T06 partial

Branch: `codex/assistant-intent-routing`, stacked on `codex/bedrock-provider`.
Status: implemented, awaiting review and live model evaluation. This is a routing
foundation, not completion of T06 or any workflow.

Added strict assistant request/route models, a versioned routing prompt, exact
command fast paths for all five intents, and bounded model fallback. The API is
`POST /assistant/route-preview`; it requires JWT authentication, accepts only the
instruction and optional hint, and always returns `execution_ready: false`.
It does not accept unvalidated mailbox context or attempt task continuation.

Schema validation rejects extra fields, write operations, duplicate JSON keys,
invented reference IDs, invalid parameter types and unsupported operation order.
Backend checks add missing source/reply/recipient/timezone/duration/action-target
preconditions. Extracted intent and numeric parameters still need model quality
evaluation and downstream semantic validation. There is no automatic model retry.
JWT subject validation now returns 401 instead of 500 for malformed identities;
validation errors omit raw request bodies and nonserializable exception context.

The prompt version is `intent-preview-1.0.0`. The full contextual 27-case planning
corpus remains a future integration evaluation: this preview deliberately has no
source snapshots, saved tasks or calendar preferences. Synthetic model fixtures
test validation and control flow, not whether a real model classifies accurately.

Verification: `ruff check app tests` passed; `python -m pytest -q` passed 83 tests,
with the same 7 PostgreSQL tests skipped. The routing slice adds 49 tests covering
all five rule paths, model-based compound proposals, boundary rejection, API
authentication and shared contract examples. Planning artifact validation also
passed (15 Markdown files, 24 work packages, 9 schema examples, 27 seed cases).

Reproduce locally with Python 3.12+ and backend development dependencies:

```bash
cd backend
python -m pytest -q
ruff check app tests
```

CI provides PostgreSQL for the seven integration tests. A local disposable test
database can be selected using `THREADLY_TEST_DB`; the existing test fixtures
drop/truncate tables, so never point this variable at a live mailbox database.

Next implementation branch: T05 durable task/job storage plus T02 source fidelity;
then bind authorized snapshots and integrate the first summary task. Add the
Flow registry/invocation adapter after AWS configuration and live smoke tests.

## Durable summary workflow — T05/T08 partial

Branch: `codex/durable-summary-tasks`, stacked on `codex/assistant-intent-routing`.
Status: implemented and locally tested; team review, live model evaluation and
frontend integration remain pending. This does not complete the action/approval
portions of T05 or all acceptance criteria of T08.

Implemented the first executable assistant path: authenticated snapshot capture →
atomic task/job acceptance → separate worker → validated summary artifact → saved
task state/event replay. Added owner-scoped history, cancellation, request hash
conflicts, recovery after expired leases, bounded provider retries and source ID
validation. A browser disconnect does not cancel execution. Artifacts contain
overview, decisions, inferred action suggestions, questions and source references.

The worker pins `summary-task-1.0.0` plus prompt/configuration hashes. Context copies
bounded synced excerpts in stable message order; later sync changes cannot switch
its input. This avoids relying on the legacy thread-head cache but does not repair
Gmail sync completeness. Coverage stays partial and discloses truncated/omitted
messages. The legacy summary endpoint remains unchanged.

Migration `8f3a7c2d901b` adds five tables with owned composite foreign keys, unique
request/artifact constraints and lease/state checks. The separate worker is enabled
through an optional Compose profile; it is not automatically launched by FastAPI.
The [API contract](../api-contract.md), [data model](../data-model.md) and
[runbook](../assistant-worker.md) document startup and recovery.

Verification on Python 3.12.14 / PostgreSQL 16: **123 tests passed, zero skipped**,
including the previously skipped integration tests. Ruff passed. Migration checks
covered empty install, model/schema drift, upgrade with existing mailbox rows and
downgrade preservation. Concurrency tests covered duplicate requests/claims,
expired-lease fencing, cancellation while the model waits, crash exhaustion,
provider retries, ownership and event replay. The new slice adds 33 tests; all
model output is synthetic and no AWS/Google write was made.

CI now requires PostgreSQL. Test setup only uses `THREADLY_TEST_DB` (or its test
default), ignoring application `DATABASE_URL`; it also prevents accidental live
Bedrock calls from inherited provider configuration. The local database was an
isolated disposable Docker container, separate from all existing user databases.

Still to implement: T02 Gmail fidelity/metadata, authorized UI selection and
ordinal reference resolution, task continuation, full Flow registry/bridge, other
intent execution, approvals/external writes, retention, operational metrics and
frontend integration. Next start with source fidelity, then bind free-text routing
to durable dispatch without bypassing these ownership/recovery boundaries.

## Gmail sync fidelity — T02

Branch: `codex/gmail-sync-fidelity`, based on the merged PR #5 commit on
`codex/assistant-intent-routing`. Status: **in review**. Local acceptance fixtures
pass; live Google account validation and team review remain pending.

Implemented pre-scan cursor capture plus paginated history replay, deterministic
provider-time/message-ID ordering, exact parsed sender matching, preserved RFC
reply metadata, monotonic thread revisions and a per-user concurrent sync fence.
Deletion/label changes reconcile mailbox scope. Changed older messages invalidate
legacy summaries, and generation cannot republish a cache after its source changes.
New snapshots record source thread version while historical snapshots stay immutable.

Verification on Python 3.12.14 / PostgreSQL 16: **149 tests passed, zero skipped**;
Ruff passed. This adds 26 Gmail fidelity cases using simulated HTTP responses and
real PostgreSQL for state/concurrency. Migration tests verify an empty install,
upgrade preserving current message rows, downgrade and schema/model drift. Tests
include a blocked Gmail request while another sync commits, and sync completing
while legacy summary generation waits. Two existing dependency deprecation
warnings remain. No model prompt changed or live model evaluation was claimed.

Migration `3c6e9a1207bd` adds versions and nullable message metadata, resets cursors
for a one-time backfill, and clears regenerable legacy caches. Existing message
rows and saved assistant work are preserved. Apply matching API/worker code with
old sync processes stopped. No live database migration or deployment was performed.

See [Gmail sync lifecycle and rollout](../gmail-sync.md), [API contract](../api-contract.md)
and [data model](../data-model.md). Still outstanding: Google test-account runs,
large-mailbox/background sync, UI ordinal binding, verified aliases and retention.
Gmail reads are simulated in tests; no real mail/calendar write was performed.

Next: T06 bind authorized context and free-text routing to durable task dispatch,
then add bounded reply/compose execution and exact human approval services.
