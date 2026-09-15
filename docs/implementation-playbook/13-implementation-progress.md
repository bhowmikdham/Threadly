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

## Contextual routing into durable tasks — T06/T08 partial

Branch: `codex/contextual-task-routing`, based on the PR #6 merge into
`codex/assistant-intent-routing`. Status: implemented and locally tested; review
and live model evaluation pending. T06 remains in progress because UI reference
maps, full continuation and capability integration are not complete.

The request API now saves free-text requests before inference, including requests
without selected context. The worker classifies all five intents, binds only
owner-authorized snapshot IDs, checkpoints the validated route under its lease,
and dispatches the installed summary path. Summary preferences are passed with
immutable excerpts. Missing information becomes a saved clarification; unsupported
and uninstalled workflows are distinct from execution failure. Compound operations
are not partially executed. English ordinal references stop for selection rather
than guessing current UI order.

Release `contextual-task-1.0.0` pins base router `intent-preview-1.1.0`, contextual
policy/schema/reference fingerprints, small/main model settings and summary policy.
Actual routing model provenance is saved. Retries reuse checkpoints; cancellation,
expired claims and deleted owners cannot checkpoint or publish. Original queued
`summary-task-1.0.0` tasks retain the original prompt and bypass the new classifier.
There is still no Flow invocation or external mail/calendar write.

Verification: **176 tests passed, zero skipped**, using Python 3.12.14 and an
isolated PostgreSQL 16 database. Ruff passed. Added 27 synthetic routing/dispatch
and PostgreSQL lifecycle cases, including context-free requests, all five intents,
compound plans, source isolation, missing UI maps, generation retry checkpoints,
routing failure budgets, cancellation and expired routing claims. Extended migration
checks verify old queued-task preservation and rejection of a downgrade that would
lose contextual tasks. Two existing dependency deprecation warnings remain.
Synthetic replay establishes control-flow and schema behavior; it does not measure
Bedrock classification accuracy, summary preference-following or citation quality.

Migration `b7a219c40e6d` adds nullable route/intent hint, optional owned context and
two stopped task states. Stop older API/workers before migrating and run matching
new versions. Downgrade refuses while contextual tasks exist. No live migration
or deployment was performed. See [routing handoff and diagram](../assistant-routing.md),
[API contract](../api-contract.md), [data model](../data-model.md) and
[worker runbook](../assistant-worker.md).

Next execution slice: bounded reply/compose draft artifacts using the dispatcher.
Still required alongside that work: UI reference binding, in-place clarification
continuation, granted capabilities, Bedrock Flows/Google integration and live evals.
Human-approved external sending/calendar creation remains a separate gated service.

## Initial reply/compose drafts — T10/T11/T13 partial

Branch: `codex/reply-compose-drafts`, based on the merged PR #7 branch. Status:
implemented and locally tested; review and live model evaluation pending. These
packages remain in progress because revisions, approvals, retrieval, full reply-all
resolution and external integration are not complete.

The assistant API accepts explicit To/Cc/Bcc literals and an optional exact reply
message. Ownership, snapshot membership and current local thread version are
checked before binding the reply. The connected sender, recipients, subject and
reply identity are copied into the task. The model supplies only subject/body,
missing facts and source numbers; it cannot change envelope addresses or the reply
subject. Context-free compose works, and background mail does not make it a reply.

The worker saves initial immutable draft artifacts with user/source evidence and
visible unresolved fields. No Gmail draft, editor insertion, send, attachment or
approval record is created. Calendar-dependent compound requests do not fall back
to plain drafting. Cancellation, fenced publication and checkpoint retries are
shared with summary tasks. Older request hashes and both previous workflow releases
remain supported; new requests pin `contextual-task-1.1.0` with draft prompt/schema
fingerprints for `draft-artifact-1.0.0`.

Verification: **206 tests passed, zero skipped**, Python 3.12.14 / isolated
PostgreSQL 16. Ruff passed. Added 30 synthetic draft/envelope and lifecycle cases:
recipient/header validation, stale/cross-owner targets, subject integrity, invalid
citations/model fields, missing facts/placeholders, background-context compose,
retry/cancellation and old-release replay. Extended migration checks preserve old
drafts/tasks and refuse loss of draft bindings on downgrade. Two existing dependency
deprecation warnings remain. Tests establish schema/control-flow behavior, not live
Bedrock factual quality, promise detection, attachment-claim accuracy or writing style.

Migration `c6e0419a72df` adds nullable `assistant_tasks.draft_input`; legacy draft
rows remain untouched and new artifacts have a separate UUID namespace. Stop old
API/workers, migrate and run matching code. Downgrade refuses while draft-release
tasks exist. No live migration or deployment was performed. See [draft handoff](../assistant-drafts.md),
[API contract](../api-contract.md) and [data model](../data-model.md).

Next: persistent editable draft revisions and exact review state, then approved
sending/reconciliation. UI integration, attachments, Calendar, retrieval, in-place
continuation, Bedrock Flows and live model evaluation remain separate work.

## Persistent draft revisions and review — T10 partial

Branch: `codex/draft-revisions-review`, based on PR #8 merge `9059e36` into
`codex/assistant-intent-routing`. Implemented and locally tested; team review and
frontend integration remain pending. T10 remains in progress: contact resolution,
artifact-to-action proposal and executable send approval are not implemented.

Users can save full subject/body/To/Cc/Bcc edits as immutable numbered artifacts,
read revision history and acknowledge review of one exact saved hash. The task
keeps its original input; each artifact has its own envelope. Concurrent edits
serialize under the task lock and stale saves fail. Replayed edit IDs return the
original revision without duplicating history. Latest-task/artifact views and
redacted edit/review events let the frontend track changes after generation ends.

Review is an acknowledgement only, never permission to send. Edits, changed local
reply source or changed sender make effective review stale. Unresolved fields,
common placeholders and missing reply headers block review. User-edited text uses
user-input evidence with parent provenance rather than retaining model citations
as proof of modified claims. Semantic fact/attachment-claim validation remains
limited; future execution requires independent validation and exact action approval.

Migration `e9b7120c4a63` adds revision envelopes/edit keys and owned review records,
backfills existing draft envelopes, preserves initial generated text/legacy drafts,
and refuses downgrade while edits or reviews exist. Matching API/workers must run
after migrating with old processes stopped. No production migration/deployment.

Verification on Python 3.12.14 / isolated PostgreSQL 16: **233 tests passed, zero
skipped**; Ruff and planning validator passed. Added 27 edit/review cases including
concurrent saves/reviews, stale revision/hash rejection, recipient isolation,
immutable history/pagination, local source/sender invalidation, blocked placeholders,
missing headers, summary rejection and deletion cascades. Migration tests cover
empty install/schema drift, preserving existing text and envelope backfill,
review-only and edit-only downgrade refusal, and allowed rollback with initial
artifacts preserved. Two existing dependency deprecation warnings remain.
Generation prompts, release selection and model behavior are unchanged.
No live AWS/Google calls or live quality evaluation.
See [revision API, diagram and rollout](../assistant-draft-review.md) and
[remaining backend delivery checklist](../backend-remaining-work.md).

Next: outgoing action proposal, exact Send approval and Gmail execution/reconciliation.
Calendar, bounded other/planning, UI context/continuation, Bedrock Flow invocation,
live model evaluation and operational release gates remain outstanding.

## UI message maps — B07 / T06 and T17 partial

The backend now accepts versioned visible-order/selection maps, hydrates owned
message excerpts, and resolves supported message lookups/summaries against the saved
view. Native exact answers cite one message; model/Flow summaries receive only that
message. Historical snapshot/releases remain available. No frontend adapter,
clarification continuation or broader inbox/option reference map is claimed complete.
See [the runtime contract](../ui-context-mapping.md) and
[B07 acceptance checkpoint](../backend-execution/checkpoints/B07.md) for evidence.

## Concise summary correction — T07/T18 partial

A user-reported prototype summary prompted a versioned issue-first summary policy,
word/repetition limits and a separate one-Flow console experiment. Native/Flow
application summaries share the policy; historical queued jobs retain old behavior.
The local full suite passes 345 tests with no skips; this is not evidence of live
Haiku relevance or factual quality. Six synthetic cases and a human-review report
path are included. See [diagnosis and deployment steps](../summary-quality.md) and
[checkpoint](../backend-execution/checkpoints/summary-quality.md).


## B08 — typed durable clarification

The backend slice on `codex/durable-clarification` preserves the original request
while accepting typed answers on the same task. It adds versioned questions,
append-only input history, effective source/envelope bindings, cancellation,
expiry, idempotent requeue and explicit unsupported outcomes for uninstalled work.
Historical tasks retain their prior behavior. See [the runtime contract](../assistant-continuation.md)
and [B08 checkpoint](../backend-execution/checkpoints/B08.md) for migration and tests.
T05/T06/T17 remain in progress; compound execution and live frontend/provider
integration are not completed by this continuation slice.


### B09a — explicit saved-source assistance

After PR #20 (`ba040b6`), the bounded-read branch implements native help, literal
search within an owned capture and selected-message rewrite suggestions. Source
checks, replayable fixtures and migration evidence are recorded in
[the runtime contract](../assistant-bounded-reads.md) and
[B09 checkpoint](../backend-execution/checkpoints/B09.md). B09/T09 remain in progress:
mailbox-wide retrieval, entity/commitment extraction/corrections and natural-language
read planning still need implementation; live rewrite quality is unverified.
