# Data model — PostgreSQL

Owner: backend. SQLAlchemy models live in `backend/app/db/models.py`; schema
changes go through alembic (`make db-revision m="..."` then `make db-upgrade`)
and update this file in the same PR. Baseline migration: `26902c33da74_w1_initial_schema`
(all 7 tables + the drafts GIN index), validated against a clean postgres 16.

Column lists below are the v0 starting point — expected to evolve.

Bedrock migration: `summaries.model_used` remains VARCHAR(80). Provider/model/prompt
labels that exceed 80 characters (for example inference-profile ARNs) are stored
as `sha256:<digest>` of the full label. Reproduce it from deployment configuration
using `GenResult.storage_label`; this is provenance, not a new cache key. No schema
migration is required for the provider adapter.

| Table         | Purpose                          | Keys & rules |
|---------------|----------------------------------|--------------|
| `users`       | account + oauth + sync cursor    | `google_sub` UNIQUE; refresh + access tokens stored Fernet-encrypted (`auth/crypto.py`) with `access_token_expires_at`; `gmail_history_id` = incremental sync cursor |
| `threads`     | conversation index               | UNIQUE `(user_id, gmail_thread_id)`; caches `last_msg_id`, `last_msg_at` for ordering + summary cache key |
| `messages`    | cleaned message bodies           | UNIQUE `(user_id, gmail_msg_id)`; `body_clean` = quotes/signatures stripped by sync worker; raw bodies are NOT stored |
| `summaries`   | thread summary cache             | **cache key = UNIQUE `(thread_id, last_msg_id)`** — source changes invalidate all cached rows for that thread; publication checks the captured thread version |
| `entities`    | extractor output (structured)    | **UNIQUE `(user_id, type, key)`** — upsert on conflict; `source_msg_id` for provenance; served to the UI with NO model call |
| `commitments` | who owes what, cross-thread      | `direction` = user_owes / owed_to_user; `status` = open/done/lapsed; dedupe on `(user_id, source_msg_id, fingerprint)` |
| `drafts`      | generated drafts + approval state| `status` = draft/approved/sent/discarded; **GIN FTS index on `body`** for "what did I already say about X" |

## Durable assistant tables (migration `8f3a7c2d901b`)

The migration adds five tables and does not modify existing mailbox rows. The
upgrade/downgrade test covers empty install, model/schema drift and preservation
of existing messages. Downgrading removes all state in the five new tables.

| Table | Purpose and constraints |
|---|---|
| `context_snapshots` | UUID string ID, owner, thread FK, SHA-256 source hash and JSONB immutable excerpt payload. Unique `(id,user_id)` enables owned references. Thread/user deletion cascades. |
| `assistant_tasks` | UUID ID, owner, request ID/hash, instruction, nullable owned context FK, nullable intent hint/route checkpoint, state, optimistic version, latest event sequence, pinned workflow/prompt/config fingerprints and sanitized error code. Unique `(user_id,request_id)`; index `(user_id,created_at,id)` for history. |
| `assistant_jobs` | One row per task; owned task FK, queued/running/done, due time, attempts 0–3, lease token/deadline. Checks require both lease fields only while running. Claim index supports polling/recovery. |
| `task_events` | Append-only composite PK `(task_id,sequence)`, owned task FK, task version, kind, redacted JSONB payload and timestamp. Sequence assigned under the task row lock. |
| `artifact_revisions` | UUID ID, owned task FK, numbered immutable typed JSONB artifact/provenance and nullable revision envelope/edit key. Unique `(task_id,stream_key,revision)`; initial generation is revision 1 within each stream. See the revision migration below. |

Task-to-context and child-to-task ownership are also enforced by composite foreign
keys. Source ownership is checked when capturing context. Jobs, events and artifacts
cascade with their task when no action history references it. Action history now
blocks source/account cascades (see B02 below); otherwise deletion removes saved
assistant work and fences a worker's subsequent completion attempt.

Model calls run outside transactions. Task/job claim, cancellation and completion
all serialize through the task row lock. Completion checks current lease token,
deadline and state before atomically publishing artifact + terminal events. A
crashed worker may repeat inference; the database rejects stale publication. This
is not an exactly-once guarantee for model calls or a design for external writes.

Snapshots copy only cleaned, bounded text already authorized through sync. They
do not store raw MIME. Retention/expiry jobs remain future work; currently copies
remain until their source thread or account is deleted. SQL engine exception
logging hides bound parameters to avoid dumping these payloads into application logs.

## Contextual routing fields (migration `b7a219c40e6d`)

`assistant_tasks.context_snapshot_id` becomes nullable; non-null references retain
the composite ownership FK and cascade behavior. The separate user FK continues
to protect context-free tasks. Nullable `intent_hint` stores advisory input and
nullable JSONB `route` stores the validated bound decision, rule/model source,
provider provenance and router/contextual release versions. There is no public
route mutation API. State is widened to VARCHAR(24); its check adds
`needs_clarification` and `unsupported`, both stopped outcomes with closed jobs.

A `task.routed` event and task version increment accompany the lease-fenced route
checkpoint. Retries reuse that checkpoint; request hashes still cover original
client input. New tasks pin `contextual-task-1.0.0`; existing summary tasks keep their
release, state, context and original prompt. Downgrade refuses to proceed while
contextual tasks exist instead of deleting or reinterpreting them. See the
[routing lifecycle and migration notes](assistant-routing.md).

## Bound draft inputs (migration `c6e0419a72df`)

`assistant_tasks.draft_input` is nullable JSONB holding validated literal To/Cc/Bcc,
the connected sender email, selected reply message ID and a copied reply binding
(thread/message IDs, local version, subject and RFC Message-ID when available).
The server constructs it at acceptance; no endpoint mutates it. Request hashes
include draft options, with absent/null options omitted for old-client compatibility.

New draft results use `artifact_revisions`, revision 1, with kind `draft`. They
reference recipient positions inside the task's bound envelope. The artifact API
returns that envelope separately under ownership checks; models cannot author it.
Context-free compose permits a null artifact snapshot ID. The legacy integer-ID
`drafts` table is unchanged; no automatic conversion/link or sender integration is
implied. See [draft storage and review contract](assistant-drafts.md).

New tasks pin `contextual-task-1.1.0`; prior contextual/summary tasks retain their
old release behavior. Downgrade refuses while tasks requiring draft inputs/new
release exist, preventing silent loss of envelope bindings. Apply matching API
and worker versions after migration with older workers stopped.

## Gmail fidelity columns (migration `3c6e9a1207bd`)

- `users.sync_version`: non-null integer, starts at 0; each successful sync
  increments it while holding the user row lock. Fetches happen outside DB
  transactions, and stale versions cannot commit.
- `threads.version`: non-null integer, starts at 0; increments once per sync for
  each thread with changed stored source data. Exact replay does not increment it.
- `messages.received_at`: nullable UTC provider `internalDate`. Ordering uses this,
  then the legacy `sent_at` fallback, then opaque message ID in bytewise order.
- `messages.subject`: nullable message-level subject for deterministic head repair.
- `messages.reply_metadata`: nullable JSONB with selected original header arrays,
  parsed addresses and labels. No raw MIME/body is stored. See the
  [field contract](gmail-sync.md#reply-and-sender-metadata).

Existing messages and assistant records survive upgrade. It clears sync cursors
for one full metadata backfill and deletes regenerable legacy summary caches.
Downgrade drops new columns but cannot restore old cursor/cache values. Sync
removes deleted/out-of-scope messages while retaining empty thread rows and
immutable saved snapshots. Historical snapshot excerpts are governed by future
retention work, not automatically purged by message removal. New snapshots capture
thread version in their hashed payload; old snapshots are left unchanged.

## Chroma (vectors)

- One collection per user: `user_{user_id}_sent` — embeddings of the user's SENT
  mail only (writing-voice retrieval for RAG, module 7).
- Capped per-user document count — the t3 instance's memory is the budget.
- Chroma is disposable/rebuildable from postgres; postgres is the source of truth.

## Conventions

- Mailbox tables use integer surrogate IDs. Assistant tasks, snapshots and artifacts
  use UUID strings. Jobs use task ID as PK; events use task ID + sequence and only
  `created_at` because they are append-only. Other tables also carry `updated_at`.
- Timestamps: `TIMESTAMPTZ`, always UTC.
- Gmail ids stored as opaque strings, never parsed.
- No raw/unclean email bodies at rest; PII minimisation starts at the sync worker.

## Draft revisions and reviews (migration `e9b7120c4a63`)

`artifact_revisions` adds nullable `draft_envelope`, `edit_request_id` (128) and
`edit_request_hash` (64). Existing draft envelopes are copied from owned tasks;
summary envelopes stay null. Unique `(task_id,revision)` replaces unique task ID;
unique `(task_id,edit_request_id)` deduplicates edits and unique `(id,user_id)`
supports owned child references. Checks require positive revisions and complete
edit metadata/draft envelopes for revisions >1. Original task input stays frozen.

`draft_reviews` stores `artifact_id` (PK), `user_id`, exact `payload_hash` and
`created_at`; its composite artifact FK enforces ownership and cascades deletion.
It records review acknowledgement, not an ActionApproval or permission to send.
Only the latest revision with unchanged hash and no local blockers is effectively
reviewed. Historical acknowledgement records remain visible after supersession.

The API appends edits and reviews while locking the task, advancing task version
and redacted event sequence in the same transaction. Generation state stays
`succeeded`. As of migration `c8291e4a6f03`, readers use the explicit task final-artifact pointer; revision ordering is scoped to one stream. Old readers
and workers must be stopped during migration. Downgrade refuses with any edited
revision or review record; no user data is automatically discarded. Details and
client recovery: [revision/review handoff](assistant-draft-review.md).

## UI context payload 1.1

No table or Alembic change. `context_snapshots.payload` can additionally hold
`schema_version: 1.1`, `scope: synced_ui_message_excerpts`, the strict `ui_map`,
`truncated_message_ids` and `capture_policy`. The source hash includes the map as
well as authoritative excerpts; visible order is independent of chronological
`messages`. Both schema versions remain immutable. Ownership checks precede capture
and task submission, and existing owner/thread deletion cascades still apply.
Tasks on new snapshots wrap their native/Flow manifest in `ui-context-task-1.0.0`.
The saved route's `reference_binding` pins message ID, excerpt hash and map hash.
It cannot be retargeted during retries. See [mapping contract](ui-context-mapping.md).


## Durable typed clarification (B08)

New requests pin `typed-continuation-1.0.0` separately from their unchanged generation
release. `POST /assistant/tasks/{task_id}/inputs` consumes a current owned question
and requeues the same task with typed effective inputs. It preserves the original
instruction/request hash and does not classify the answer as a new command.
New task views expose `question`, `input_version`, `continuation_release`,
`effective_context_snapshot_id`, `effective_draft_input` and `resolved_inputs`.
Historical tasks keep their prior behavior. See [the complete API and lifecycle
contract](assistant-continuation.md) for examples, error handling and frontend forms.

Migration `f2b6049c7a81` adds task continuation/effective-context fields and the
`task_questions` / `task_inputs` tables. Owned composite FKs, one input per question,
request-key uniqueness and bounded versions guard acceptance. Effective source
deletion cascades the dependent task just like its initial source. Questions and
answers commit atomically with task/job changes; downgrade rejects remaining new
state. This adds no Calendar handler, compound executor or approval to send.


## Bounded read actions (B09a)

Explicit `AssistantRequest.read_options` adds native help, literal saved-capture
search and single-message rewriting through the existing durable task API. New
read tasks pin `bounded-reads-task-1.0.0`; other task hashes/releases are preserved.
Migration `a6417c29d805` adds nullable `assistant_tasks.read_input` and guards rollback
with retained read tasks. No external writes or mailbox-wide search are enabled.
Source ownership/freshness is checked before execution, publication and artifact
retrieval. See [request examples, lifecycle, limits and remaining B09 work](assistant-bounded-reads.md).


## Scoped local-mail search (B09b)

`POST /assistant/mail-search` adds explicit owner/date/folder search over synced
cleaned bodies, with bounded exact excerpts and signed pagination tied to the
mailbox sync version. It is a native read endpoint, not a task, model or Gmail
invocation. `GET /assistant/workflows` advertises this capability separately.
Migration `b7180d3f9e62` adds the owner/effective-date/row-ID search index, preserving
all data and prior task contracts. [Frontend contract, concurrency, coverage and
rollout](assistant-mail-search.md) describes the remaining extraction/planning gates.


## Compound steps and artifact streams (migration `c8291e4a6f03`)

- `assistant_tasks.compound_input`: nullable JSONB with the immutable explicitly
  selected template, summary dependency and draft inputs; absent for old tasks.
- `assistant_tasks.final_artifact_id`: nullable, deferred composite FK to artifact
  `(id,task_id,user_id)`. Backfilled to each task's highest historical revision;
  new tasks populate only when final output succeeds, edits move it atomically.
- `artifact_revisions.stream_key`: non-null varchar(32), default `result`; summary
  steps use `summary`. Unique `(task_id,stream_key,revision)` replaces task-wide
  revision uniqueness. UUIDs, payloads, envelopes, edit IDs and reviews are unchanged.
- `assistant_steps`: PK `(task_id,ordinal)`; owner, operation, state, attempt count,
  input hash, pinned release, output artifact/hash and sanitized error. Ordinal is
  bounded to 1–2, attempts to 0–3; states pending/running/succeeded/failed/cancelled.
  Composite FKs enforce owned task and output in the same task. Succeeded state
  requires output ID/hash; other states have neither.

Only the final draft stream supports editing. A generated step's output reference
remains immutable after a user draft edit; the task pointer identifies the edited
final result. Intermediate artifacts cannot accidentally become the newest draft.
There is no external-action table/executor in this migration. Downgrade refuses
while compound tasks/steps/non-result streams exist. See
[compound runtime](assistant-compound-workflows.md) for recovery and rollout.

## Durable action records (migration `d9302f5b7a14`)

Adds `assistant_actions`, `action_approvals`, `action_jobs` and `action_attempts`.
Composite owner/task/artifact/approval FKs use RESTRICT, so action history survives
uncoordinated source/account deletion. Exact payload/source identity and approvals
are immutable; migration-installed triggers guard state changes and dispatch intent.
Partial uniqueness allows only one unresolved attempt per action.

Parent `c8291e4a6f03`; existing artifacts/reviews are unchanged. Downgrade refuses
while any action history exists. This migration installs no executor; B03 adds proposal/read APIs.
Full fields, lock order, retention gate and tests: [action storage](assistant-action-storage.md).

## Actual Google grants and OAuth sessions (`f1a2b3c4d5e6`)

Parent `d9302f5b7a14`. Users gain nullable JSONB `google_scopes`/`google_identity`,
nullable `google_email_verified`/`google_connected_at`, connected flag, positive
`google_account_version` and `google_token_version`. Existing token bytes remain
unchanged; legacy grants/verification stay unknown. Token renewal and permission
versioning are separate.

`google_oauth_sessions` stores state hash (PK), PKCE S256 challenge, exact callback,
nullable owned user/version pair, expiry and consumption time. Owner deletion
cascades login state; action retention rules are unchanged. No raw state/code/verifier
is stored. Downgrade refuses when action history exists and otherwise invalidates
pending sign-ins while preserving legacy tokens. See [Google lifecycle](google-capabilities.md).

## Email action payload `email-mime-1.0.0` (B03, no DDL)

Existing `assistant_actions.payload` stores `preview`, `mime_base64url` and
`mime_sha256`. B02 `payload_hash` covers schema + the whole payload, and
`source_artifact_hash` covers artifact + edited envelope. `source_versions` stores
proposal request identity, effective context hashes/thread versions, reply metadata
hash when applicable, Google subject/account version and actual scopes. MIME Date,
Message-ID and 30-minute expiry are frozen per proposal. Reads never mutate payloads.
No approval, job or attempt is created. [Contract and retention boundary](email-action-previews.md).

## Action decision receipts (`a0426e9bc731`, B04)

Parent `f1a2b3c4d5e6`; adds `action_decisions` with UUID, owned action FK, operation,
request key/hash, expected version, decision and creation time. Unique
`(user_id,operation,request_id)`, valid operation/decision constraint and indexed
action/decision support replay and read status. Update/delete trigger preserves
receipts; downgrade refuses if any exist. No existing action bytes/history change.

Approval continues to use `action_approvals` and one `action_jobs` row per action,
committed together. Late cancellation adds a receipt/event without modifying
action state/version, dispatch attempts or recovery job. [Decision contract](action-approval.md).


## Dispatch lifecycle (B05, no DDL)

Reuses action tables at head `a0426e9bc731`. A running dispatch job has a lease and
up to three **preflight** claims. Before any POST, one transaction inserts an
immutable dispatched attempt then moves the action to executing. Attempt identifiers
store frozen RFC Message-ID, Gmail thread ID and payload hash. Attempts are never
reused to resend. Valid completion stores sanitized attempt evidence and the action
result (`gmail_message_id`, `gmail_thread_id`) or error code in the same transaction.
Expired committed intent becomes `outcome_unknown`; its job becomes held/reconcile.
One late nonterminal observation can be stored under `evidence.late_response` without
changing action state or overwriting terminal evidence. No new tables or migration.
[Full worker contract](email-actions.md).


## Recovery observations (B06, no DDL)

Unknown email attempts add `evidence.reconciliation` with a durable round count
(0–3) and at most three sanitized observations (`round`, `code`, UTC `at`). Each
read lease claim consumes a round before network work. Jobs retain `kind=reconcile`;
queued `available_at` schedules reads, expired running leases may reclaim only
within budget, and exhausted jobs are held. The B05 job attempt counter is unchanged.
Success merges `resolution` evidence, preserves prior/late observations, updates
attempt before action and closes the job in one transaction under existing guards.
Read observations do not rewrite the immutable action payload or approval.

## Lookup/draft streams (B10b; no migration)

`assistant_tasks.compound_input` can also hold the strict `LookupDraftRequest`
selected by its `lookup_then_reply`/`lookup_then_compose` template. New tasks pin
`lookup-draft-template-1.0.0`. Existing summary requests/releases are unchanged.
`assistant_steps` ordinal 1 uses native `search_mail`; ordinal 2 remains a draft.
Its immutable intermediate artifact uses stream `lookup`; final draft uses `result`.
The existing two-step/attempt bounds, owner FKs, dependency hashes, immutable
revision identity and final pointer apply unchanged. Migration head remains
`a0426e9bc731`; rollback requires compatible readers for stored lookup tasks.

## Reviewed command plans (migration `b10c026e9a31`)

Parent `a0426e9bc731`. New `command_plans` stores UUID/user, request ID/hash and typed
JSONB input, nullable owned context/digest, release manifest, state, nullable result
and review hash, expiry/creation times and nullable owned task ID. Unique
`(user_id,request_id)` reserves inference once; composite context/task FKs prevent
cross-owner references. States are planning/proposed/needs_clarification/unsupported/
failed/expired/consumed. Only consumed rows carry task IDs. Database triggers freeze
input/release/expiry and published result/hash and constrain state transitions.
Confirmation and existing task/job creation share one caller transaction. Old task,
artifact and action records are untouched. Source/task deletion cascades its plan;
downgrade refuses while any command-plan history exists.

## Calendar read storage (migration `c12026e9a032`, B12)

Parent `b10c026e9a31`. `calendar_preferences` has one user-owned row (user_id PK/FK),
positive version/account_version, policy_version, typed JSONB preferences and
created/updated times. A trigger prevents ownership/creation-time changes and
requires version +1 on every update. API updates serialize on user then preferences.

`calendar_evidence` has UUID ID, user_id FK to the owner's preference row, positive
preference/account versions, policy version, checked_at/expires_at and minimal typed
JSONB result. Owner/expiry indexes support reads and future retention. Evidence is
immutable under UPDATE; the API exposes no deletion. Deleting the user/preferences
cascades owned evidence. A query never references another user's preference ID.
Expiry invalidates reuse but does not delete rows. Downgrade refuses with either
table populated; old tasks/releases/history are preserved. [Runtime](calendar-reads.md).

## Calendar slot storage (migration `d13026e9a033`, B13)

Parent `c12026e9a032`. Adds `calendar_slot_requests`: UUID id, owner FK, unique
(owner, request_id), canonical request hash/JSON, immutable anchor time and optional
owned parent receipt, preference/account/policy versions and preference snapshot,
resolution JSON, lifecycle state, optional evidence FK, calculation time, result JSON,
sanitized error code, created/expiry timestamps. Slots are JSON entries with stable
UUID5 IDs, exact UTC instants and local labels. Evidence checked_at remains in the
linked B12 record. No reservation or provider event ID is created.

Composite owner FKs prevent foreign evidence/anchor references. The migration adds
(id, user_id) uniqueness to evidence to support that FK. Positive versions and expiry
ordering are checked. A trigger permits only processing → terminal publication and
expiry shortening; inputs, anchors, resolution and published results cannot be updated.
Account → preference → receipt lock ordering serializes idempotency and publication.
Downgrade refuses with receipts present and preserves existing B12 data when empty.
Expiry is a use restriction, not retention cleanup. [Lifecycle](calendar-slots.md).

## Meeting negotiations (migration `e14026e9a034`, B14a)

Parent `d13026e9a033`. `meeting_negotiations` stores owner/thread, immutable creation
key/hash/policy, current version/state, owned current offer/selection pointers,
close receipt and creation time. `meeting_offers` stores immutable offer revision,
creation version, thread version and owned B13 slot query (which preserves exact
options/assumptions), request key/hash and creation/expiry. `meeting_selections`
stores offer/slot identity, request key/hash, reserved negotiation version,
checking/terminal state, optional owned fresh B13 query, error and creation/expiry.

Named composite FKs protect thread and current-pointer ownership, same-negotiation
offer linkage, source query ownership and check ownership. B13 nested slot identity
and exact recheck payload are validated by service code. Uniqueness serializes
request keys and offer revisions. Triggers freeze offers and completed selections,
allow receipt expiry only to shorten, and require exactly advancing negotiation
versions with immutable identity; closed rows cannot reopen. No event IDs, actions,
approvals or worker jobs are created. Thread sync now uses actual update-clock time
for new-source vs older-query checks. Downgrade refuses with negotiation records;
existing slot queries survive legal empty rollback. [Lifecycle](meeting-negotiations.md).
