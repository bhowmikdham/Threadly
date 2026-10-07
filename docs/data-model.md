# Data model — PostgreSQL

On-demand [badge classification](classification/README.md) adds no tables or migration.
It reads account/session state, holds mail and model output in request memory, then
returns validated labels and source provenance. It does not write Message, Thread,
ContextSnapshot, job or classification records, nor update `threads.needs_reply`.
There is no persistent classification cache or saved user override in this slice.

Shared email context adds no tables or migration. `context_snapshots.payload`
also supports internal `storage=gmail-context-plan-1.0`: primary thread/version
metadata, up to five owner-bound source references, explicit reply-message ID,
policy and prompt hash. `source_hash` binds deterministic materialized excerpts;
original mail bodies remain transient. The relational thread anchor identifies
the primary source, not every supporting source. Encrypted conversation state adds
bounded `context_order`, monotonic `next_context_reference`, reference-only
`context-N` entries and per-turn `context_references`.
See [shared context](shared-mail-context.md).

> Current conversation layer: [context, lifecycle and limits](contextual-conversation.md).

> Current correction: [on-demand Gmail](on-demand-gmail.md) supersedes the mailbox-sync,
> local-search and stored-source assumptions below. Default runtime fetches selected sources
> from Gmail and stores references only; bulk sync is retired. See that contract before integration.

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
| `context_snapshots` | UUID string ID, owner, thread FK, SHA-256 hash of the validated transient excerpt and JSONB source-reference payload. In on-demand mode the payload stores owner/account version, Gmail thread ID, fingerprint and optional ordered message IDs, not source text. Unique `(id,user_id)` enables owned references. Thread/user deletion cascades. |
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

In current on-demand mode, snapshots copy no source body, subject, address or raw MIME.
Workers refetch the bounded source from Gmail and compare the account version,
fingerprint and hash before use. Legacy body-bearing snapshots can exist only on the
explicit legacy mode/rollback path and are rejected by on-demand readers. Snapshot and
artifact retention/expiry jobs remain future work; references and generated artifacts
remain until their owning source/task/account lifecycle removes them. SQL engine exception
logging hides bound parameters to avoid dumping payloads into application logs.

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

`users.google_account_version` is the Google connection generation. A
login/reconnect or disconnect changes it; ordinary Google token renewal does not.
Migration `f28026e9a040` adds positive `users.threadly_session_version` (default 1)
as a separate sign-out generation. Owned `google_oauth_sessions` also store this
generation; migration discards legacy owned OAuth states so they cannot mint a
new bearer from an older JWT. `POST /auth/logout` increments it under a user
row lock, leaving Google credentials, Calendar preferences and action account
versions intact, and deletes pending owned OAuth states. An in-flight consumed
state still fails the generation check at code exchange. Every protected request reads the connected user and both
generations from PostgreSQL and rejects a stale or pre-generation bearer. The
migration downgrade refuses by default even when all users are disconnected:
signature-only rollback code could accept an old bearer for a disconnected or
deleted user. For manual rollback, stop every API instance and auth issuer first,
rotate `SECRET_KEY` while issuance is stopped, then set both
`THREADLY_AUTH_SERVICES_STOPPED=1` and `THREADLY_SESSION_SIGNING_KEY_ROTATED=1`
for the offline Alembic downgrade. The flags record an operator assertion;
they do not verify shutdown or key rotation.

## OAuth ingress counters (`c33026e9a040`)

Parent `f28026e9a040`. `oauth_rate_limits` holds one-minute fixed-window counters
for Google begin/exchange per observed peer and per route globally. Its primary key
is a keyed SHA-256 digest; no raw IP, Google code, email or OAuth state is stored.
The API deletes expired rows in batches before each attempt, and saturated counters
stop growing until their window resets. The counter transaction commits independently
before OAuth state consumption, so rejected and provider-failed attempts still count.
Missing schema or database access closes public sign-in with a 503 response.
Successful begins also prune up to 100 expired OAuth states per call; missing or
expired state remains invalid for exchange.

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
`lookup-draft-template-1.2.2`. Existing summary requests/releases are unchanged.
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
immutable under UPDATE; the API exposes no deletion. Its `start`/`end` and busy
intervals retain the original exact query window; provider transport precision
padding is not stored as evidence coverage. No schema change is needed for this
adapter correction. Deleting the user/preferences
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

## Assistant scheduling (migration `f14026e9a035`, B14b1)

Parent `e14026e9a034`. Nullable `assistant_tasks.scheduling_input` saves the typed
original request, preference snapshot/version, account version, original request or
message date anchor, and source hash. A trigger forbids changing this accepted
input, including attaching it to an old task. A check requires the scheduling
release/intent and excludes compound/read/draft inputs. Older task rows remain null;
original request hash calculation excludes the new field for older APIs.

Existing owned task/job/question/input/artifact tables implement scheduling state;
no new table is added. Questions carry requested fields, choices, route hash and
saved anchor. Append-only typed answers overlay the original constraints. The task
effective context stays fixed; a new source needs a new task. Worker publication
uses its existing lease fence plus current source/Calendar checks. Artifacts point
to owned B13 queries in their payload/provenance and are validated against the saved
query; B14a adoption still enforces its own query/owner/thread/version rules.

Downgrade refuses while any scheduling task exists. Legal empty rollback preserves
older assistant history and Calendar receipts. No migration deletes user data.
[Runtime lifecycle and concurrency](assistant-scheduling.md).

## Scheduling proposals (migration `f14026e9a036`, B14b2a)

`scheduling_proposals` persists one owner/request-key interpretation receipt before
model inference. It records immutable original request, context/hash, saved Calendar
binding (account/preferences/date anchor), release, 15-minute expiry, state,
result/hash and optional consumed task. Composite context/task foreign keys preserve
ownership. Input and completed result immutability and legal transitions are guarded
by PostgreSQL trigger. Exact confirmation and task insertion commit together.
Downgrade refuses while proposals exist; historical typed scheduling tasks survive
an otherwise empty rollback. See [contract and state diagram](scheduling-extraction.md).


## MVP integration migrations

`a17026e9a037` (parent `f14026e9a036`) adds nullable immutable
`assistant_tasks.workflow_input`; only `mvp-workflow-1.0.0` tasks can use it, mutually
exclusive with old scheduling/read/compound input. Step ordinal 3 requires this
release; old tasks keep the two-step bound. Plan artifact revisions reuse edit
request/hash columns and require `action-plan-1.0.0` provenance. Draft edit rules
remain unchanged. Downgrade refuses retained MVP workflow/plan data.

`b17026e9a038` adds `mail_sync_jobs` (owned idempotent request, one active job/user,
account/sync versions, phase/cursor, retry budget, lease and sanitized outcome) and
`mail_sync_stage` (owned composite FK, one cleaned payload/deletion per job/message).
Running jobs require a lease; other states have none. Cursor/staging checkpoints
commit together; mailbox publication and history advancement are atomic. Active
sync jobs block downgrade. Terminal staging can be conservatively cleaned; jobs
and action audit records are retained.

Master and meeting-response proposals reuse `command_plans` with distinct pinned
release identifiers. Calendar event actions use existing action/approval/attempt/job
tables with `create_event` + `calendar-event-1.0.0`; email approvals cannot authorize
that schema. New records do not reinterpret historical queued releases. See
[mappings and lifecycle](mvp-workflow-map.md).

## Conversation state — migration c23026e9a039

`conversations`: UUID primary key, user FK with cascade, account version, monotonic version,
Fernet `state_enc`, seven-day `expires_at` from creation (extended only to protect an
acquired turn lease), lease ID/until, unfinished request ID/hash,
and created/updated timestamps. Indexes on owner and expiry. Encrypted state holds at most
12 dialogue exchanges plus 12 idempotency receipts, at most 25 ordered search refs,
pinned context ID, current task/proposal references and crash-recovery result references.
Search refs include Gmail message/thread IDs and the last filter/cursor; they exclude original
provider bodies, subjects, snippets and model tool transcripts. Generated assistant text,
user instructions and minimal citations may contain email-derived information. No new
embedding or mailbox table is added. Normal turns do not renew the fixed expiry; an active
lease only prevents cleanup during an in-flight turn. Expired rows are inaccessible and the
assistant worker purges eligible rows hourly. Browser session storage separately retains the
full exact unfinished turn for safe retry. Deleting the conversation does not delete existing
tasks/artifacts/action audit records and does not erase existing backups.
Downgrade refuses to drop retained conversations; disabling the feature is the safe rollback.


Calendar evidence JSON may now include nullable `display_name` on each calendar
coverage entry, populated from the current owner-authorized Calendar list. Old
evidence without that optional field remains compatible. No schema migration.

### Calendar conversational read state (1.3.0)

No migration or new table. The encrypted `Conversation.state_enc` object gains a
`calendar_read_anchor` ISO timestamp set at the first claim of each turn. It is
retained for crash retries with the same request ID/hash and replaced for a new
turn; a legacy unfinished turn without this field acquires it on the next claim.
This pins relative dates without trusting model timestamps. Existing leases,
account ownership, request hashes and version checks remain authoritative.

New Calendar read answers have history `source: calendar_tools`. Their display
text is retained in encrypted history/receipts, but filtered out of model history.
Transient `calendar_tools` observations are excluded from receipts/checkpoints;
no provider event bodies/IDs or event cache are added. Free-slot/busy-time reads
reuse owned `CalendarEvidence` with the existing preference/account versions and
five-minute expiry. Returned conversational free slots are suggestions, not
`CalendarSlotRequest` offers, reservations or approvals.


Calendar recovery adds no tables or migrations. `PreferencesOut.needs_review` is computed
from the existing preference account/policy versions. Encrypted conversation history
entries may additionally contain nullable `error_code`; the field contains a server error
identifier, not Calendar provider payloads. Existing history without this field still loads.

### Calendar request continuation (1.4.1)

`Conversation.state_enc` may contain one `calendar_read_request`: tool name, original
user instruction, validated typed arguments, original date anchor and last request ID.
Whole-day results canonicalize the saved date to the backend-resolved civil date.
Legacy recovery temporarily stores null arguments plus the conversation creation
timestamp as a conservative lower bound on the original anchor. No events, busy
intervals, calendar IDs or provider prose are added to this request record.

The existing owner/version/lease checks, encryption, bounded state and seven-day
conversation expiry apply. Completion clears this record when the turn did not
continue a Calendar read. Valid retries retain it through recoverable failures;
idempotent request receipts still prevent duplicate reads for the same turn. No
schema migration or extension storage change is needed.

### Persistent login credentials

No database schema or additional Google token storage is introduced. The renewal
JWT is a bearer credential with a fixed expiry and the same user/account/session
generations as the access JWT. New access JWTs also bind the fixed session expiry.
Every renewal rechecks the live user under the existing row lock, so disconnect,
logout and account changes invalidate saved credentials. The extension's durable
record is origin-bound and restricted to trusted extension contexts; its mailbox
content and transient conversation state are not moved into durable storage.


### Chat Calendar approval and direct event candidates (1.5.0)

Migration `c061026e9041` follows `c33026e9a040`. `conversations` adds
`calendar_approval_mode` (ask/always, default ask), `calendar_approval_version`
(default 0) and nullable `calendar_approval_session_version`. Existing rows remain
Ask. The mode is independent from turn versions and fenced by the User lock at
mutation and write dispatch. A new sign-in generation makes an old grant ineffective.
Downgrade removes these fields; it does not remove action audit records.

Direct events use an immutable `calendar_event` artifact in a completed
`direct-calendar-event-1.0.0` task, then existing `AssistantAction`, exact
`ActionApproval`, `ActionJob` and `ActionAttempt` records. Sources include
`direct`, conversation ID, account/subject/preferences/session versions and saved
approval mode/version. Always permission is checked again before dispatch. Stable
request-derived task/artifact/provider IDs and a transactional conversation checkpoint
prevent duplicate candidates after an interrupted response. The frozen event and its
exact hash remain the authorization target; no new external-write executor exists.
Pending user event details (15-minute expiry) stay in encrypted conversation state;
complete event previews are not duplicated into conversation receipts/history.


### Conversation recovery and timed reads (1.7.2)

No table or migration is added. Recovery finalizes `state_enc.pending_result` into
an existing bounded receipt with the original request ID/hash and next conversation
version, then clears the pending/lease fields. Childless cancellation records a
terminal cancellation receipt under the same key; linked work is retained. Replaying
an already completed receipt does not advance the version. `recovered_request_id`
is response metadata; recovered history entries add `recovered: true` with an empty
user string so generated text is never converted into user authority. Existing
retention, encryption, ownership and compaction limits apply.

`calendar_read_request` can also retain `check_time_availability` with its literal
clock source, duration and resolved civil date. It stores no provider event content,
busy intervals or new calendar identifiers. Direct creation policy 1.1.0 still uses
the same immutable artifacts/actions and exact approval records introduced in 1.5.0;
previously queued payloads are not reinterpreted.


Pending creation additionally retains its original validated user request/field sources,
a bounded list of previous user-supplied calendar names, and optionally up to ten
`calendar_choices` (opaque choice ID, owned provider calendar ID and display label,
account/preference versions). `selected_calendar` stores the explicitly chosen
identity and versions while other event fields are clarified. This metadata remains
in the encrypted, expiring conversation state; no event contents are cached. Provider
IDs and internal authority metadata are excluded from model context/public choices.
Choice references share the pending event's 15-minute expiry and current turn version;
current ACL is rechecked at selection and again before eventual event dispatch.

Conversation encrypted JSON's optional `calendar_event_request` version 2 contains
`goal_id`, `revision`, typed `arguments`, `field_provenance` (request ID, operation,
source), original creation authority, a pinned date anchor, fixed expiry, optional
owned calendar choices/selection, and optional immutable `action_id`. Current field
evidence replaces obsolete values; prior quoted/provider text is not reconstructed
as authority. Legacy structured pending drafts remain readable. Unstructured old
creation chats require clarification rather than guessing an old relative date.
No relational schema change is introduced.

### Gmail draft save receipts

`gmail_draft_saves` (migration `g071026e9042`) stores one user-clicked outgoing
snapshot per `(user_id, source_key)` and deduplicates `(user_id, request_id)`.
It retains verified account generation, immutable validated MIME/preview, source
artifact revision/hash or conversation draft/version provenance, provider result
IDs, and `saving|succeeded|failed|outcome_unknown` state. These generated/user-edited
payloads are distinct from original mailbox bodies. Account deletion cascades;
receipt history otherwise remains durable so retries cannot duplicate writes.
The table is never read by the send worker and grants no send authority.

### Isolated chat archive prototype (not deployed)

Migration `h071026e9043`, after `g071026e9042`, adds `conversation_exchanges`:
`(conversation_id, version)` primary key, unique `(conversation_id, request_id)`,
positive version constraint, and encrypted `payload_enc`. The foreign key cascades
only with chat deletion. Payloads contain original generated/user dialogue and
identity-only mail source handles, not raw tool observations or provider snippets.
Conversation completion archives exchanges atomically before state compaction;
lazy backfill preserves only still-retained pre-upgrade history. Downgrade refuses
to drop a nonempty archive. Expired chats are inaccessible; explicit retention
cleanup can remove them after its configured grace period without deleting tasks,
artifacts or action audit/recovery records. No automatic cleanup schedule is added.
See [design, invariants and limitations](chat-context-design-review.md).

### Conversational draft continuity (1.8.7)

Encrypted `email_draft_goal` additionally retains `origin_request_id` and, after
text generation, `draft_id`. These identify user-only recovery context and the
current structured editor. No migration is required. Conversation history may
include read-only `email_draft_review` receipt/status guidance; it is not write
authority. Source/account ownership and frozen Gmail draft-save receipts are
unchanged.

### Mail retrieval and reply goals (1.8.8)

Optional encrypted conversation keys `mail_goal` and `mail_reply_goal` retain
bounded USER instructions, literal entity/sender constraints, date anchor and
timezone, folder scope, ordering/purpose, candidate reference dispositions,
page count and preparation status. The reply goal binds a source reference and,
after submission, an owned task ID. They contain no original mail body, snippet,
assessment quote or generated recipient address. New searches reset candidate
assessments; new goals replace retained constraints. A continuation preserves
scope; changing it requires an explicit new goal. Existing account/version/lease
and conversation expiry fences apply. No schema migration is required. Runtime
search cards remain transient; filtered card ordinals cannot rename source IDs.

## Independent chat goals (unpublished context prototype)

`conversation_goals` is added with `conversation_exchanges` by `h071026e9043`.
Its composite key is `(conversation_id, goal_id)`; deletion cascades from the chat.
Kind, retained/closed status and updated version are indexed metadata. The typed
goal payload, bounded label and identity-only source references are Fernet encrypted,
with a hash to avoid rewriting unchanged goals. It is not a universal user-facts schema.

Task/proposal conversation provenance may contain `user_context_enc`, encrypted
verified quotes selected for this requested work. Workers decrypt it only for
generation prompts, independently of the instruction used for operation routing.
No original provider message bodies enter this field. See the context design review
for legacy backfill, retention and rollback limitations.
