# Data model — postgres (12 tables)

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
| `artifact_revisions` | UUID ID, owned task FK, revision 1, typed JSONB artifact and provenance. Unique task ID ensures one published summary. No update endpoint; revision editing is a later migration. |

Task-to-context and child-to-task ownership are also enforced by composite foreign
keys. Source ownership is checked when capturing context. Jobs, events and artifacts
cascade with their task; deleting an account or source thread removes its saved
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
