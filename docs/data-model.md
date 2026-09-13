# Data model — postgres (7 tables)

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
| `summaries`   | thread summary cache             | **cache key = UNIQUE `(thread_id, last_msg_id)`** — new message ⇒ new key ⇒ regeneration; old rows are cheap history |
| `entities`    | extractor output (structured)    | **UNIQUE `(user_id, type, key)`** — upsert on conflict; `source_msg_id` for provenance; served to the UI with NO model call |
| `commitments` | who owes what, cross-thread      | `direction` = user_owes / owed_to_user; `status` = open/done/lapsed; dedupe on `(user_id, source_msg_id, fingerprint)` |
| `drafts`      | generated drafts + approval state| `status` = draft/approved/sent/discarded; **GIN FTS index on `body`** for "what did I already say about X" |

## Chroma (vectors)

- One collection per user: `user_{user_id}_sent` — embeddings of the user's SENT
  mail only (writing-voice retrieval for RAG, module 7).
- Capped per-user document count — the t3 instance's memory is the budget.
- Chroma is disposable/rebuildable from postgres; postgres is the source of truth.

## Conventions

- All tables carry `id` (surrogate PK), `created_at`, `updated_at`.
- Timestamps: `TIMESTAMPTZ`, always UTC.
- Gmail ids stored as opaque strings, never parsed.
- No raw/unclean email bodies at rest; PII minimisation starts at the sync worker.
