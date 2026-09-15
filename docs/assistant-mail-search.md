# B09b — Scoped search of locally synced mail

Based on merged PR #21 (`9b3b14e`), branch `codex/scoped-mail-search`.
This adds native local mailbox retrieval across threads. B09/T09 remain in progress:
entity/commitment extraction and corrections, natural-language read planning and
compound workflow integration are still separate implementation work.

## Frontend contract

Call authenticated `POST /assistant/mail-search`. This is a bounded read response,
not a durable generation task. It makes no model, Gmail or Calendar call and saves
no draft, approval, artifact or search-history row.

```json
{
  "schema_version": "1.0",
  "query": "invoice",
  "folder": "INBOX",
  "received_from": "2026-09-01T00:00:00+10:00",
  "received_before": "2026-10-01T00:00:00+10:00"
}
```

All scope fields are required. Query is a nonblank literal substring of 1–200
characters, matched case-insensitively against **cleaned body text**. `%`, `_`, SQL,
regular-expression and Gmail query syntax remain literal text. This release does
not search subjects, attachments, remote Gmail or semantic embeddings.

Folder is exactly `all_synced`, `INBOX` or `SENT`. Known folder membership comes
from synced Gmail `label_ids`; it is never guessed from the sender. Unknown/legacy
label metadata is excluded by Inbox/Sent filters. `all_synced` includes locally
stored rows; sync normally excludes Spam and Trash. Other folders/labels are not
accepted yet.

Dates must be ISO timestamps with an explicit offset and a positive window of at
most 366 days. The window is `[received_from, received_before)` and dates normalize
to UTC. Search uses provider receipt time, falling back to the historical sent-time
column only where receipt time is absent. Rows with neither time cannot match.
Each result exposes `date_basis`; timezone/DST intent is not guessed from prose.

## Response and pagination

The 200 response has:

- `policy_version: scoped-mail-search-1.0.0` and normalized `scope`;
- at most 20 `results`, each with owned `message_id`, `thread_id`, `thread_version`,
  `source_version`, `received_at`, `date_basis`, `evidence_ref`, exact `quote`,
  zero-based character `quote_start` and `quote_truncated`;
- an optional `next_cursor` for the same scope;
- `coverage` with `complete: false`, `live_verified: false`, source type,
  `sync_version`, `last_synced_at`, `sync_status` and explicit exclusions;
- `empty_result_meaning` explaining the scope of an empty result, otherwise null.

Quotes are bounded to 1,000 characters around the first matching body substring.
No sender or full-body field is returned. `quote_truncated` means the quote does
not include the whole stored cleaned body. A source reference identifies current
owned retrieval evidence; it is not permission to use another user's data or a
permanent artifact ID.

Render an empty result as “No matching local text in this scope.” Never infer that
an invoice/email/fact does not exist in Gmail. Even after successful sync, coverage
is not live or complete. No semantic/extraction index completeness is implied by
the SQL search index. `last_synced_at: null` reports `not_yet_verified` even if
legacy local rows happen to be present.

Send the returned cursor with **unchanged** query/folder/dates for another page.
Results use descending effective receipt time and internal row ID as a stable tie
breaker. This order is search order, not the user's screen order. Tokens are signed,
owner/scope/version-bound, expire 15 minutes after the first page and do not extend
expiry on later pages. Token payloads are encoded, not encrypted; they contain no
email bodies or query text. A changed query, date range, folder or owner cannot
reuse one. Secret-key rotation invalidates old tokens.

If sync commits between pages, return 409 `mail_search_changed` and restart search.
Do not combine a new first page with old subsequent pages. Source bodies and labels
may have changed. A user can explicitly choose a narrower window if a mailbox is
changing frequently.

## From a result to a task

The client can capture the selected result's thread using the existing owned
context-snapshot API. For a message operation, use capture schema 1.1 with its saved
thread version and explicit selected ID, then submit the existing read or summary
request. Capture rechecks ownership/version and hydrates current stored content.
Do not pass a bare evidence string as model/tool authority, reinterpret result
positions as visual ordinals, or inject these results into an unvalidated compound
plan. Automatic retrieval → generation coordination belongs to the later planner
and compound work, using this backend service rather than model-generated SQL.

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as Search API
    participant DB as PostgreSQL
    participant SYNC as Sync worker
    UI->>API: Explicit query, folder, time window
    API->>DB: Shared user lock; read sync version
    API->>DB: Owner-filtered indexed window and literal body search
    DB-->>API: Bounded excerpts + next position
    API-->>UI: Results, partial coverage, signed cursor
    SYNC->>DB: Exclusive user lock; commit changes + version
    UI->>API: Next page with old cursor
    API-->>UI: 409 if sync version changed; restart
```

## Concurrency and budgets

The existing sync worker exclusively locks the user before committing mailbox
changes and increments `sync_version` in that same transaction. Search takes a
shared lock on that row while reading the version and results. Sync cannot mutate
that mailbox between the version check and search query; concurrent readers can
coexist. Search commits its read transaction before returning. No provider/model
call or human wait holds this fence.

Queries are parameterized and join both messages and threads to the authenticated
owner. No cross-user cache is introduced. Transaction-local PostgreSQL limits bound
lock waits to 1 second and each statement to 3 seconds. Timeout/lock-contention
responses are sanitized 503 `mail_search_busy`, not an empty successful result.
The composite index limits the owner/date scan; body substring matching is still
performed within that window. Large-corpus latency/load remains a staging gate.

| Response | Recovery |
|---|---|
| 401 | Sign in or reconnect the local account |
| 422 `validation_error` | Provide supported folder, literal query and offset-aware bounded dates; remove unknown fields |
| 409 `mail_search_cursor_invalid` | Expired, altered or mismatched token; restart the original scope |
| 409 `mail_search_changed` | Sync advanced; restart from the first page |
| 503 `mail_search_busy` | Retry or narrow scope; do not show “no matches” |

## Storage, compatibility and remaining gates

Migration `b7180d3f9e62` follows `a6417c29d805`, adding the B-tree index
`ix_messages_owner_search_time` on `(user_id, coalesce(received_at, sent_at) DESC,
id DESC)`. No row fields, task hashes or historical prompt/read releases change.
Downgrade drops only the index; mailbox and task data remain. The ordinary index
build can block writes while migration runs, so stop API/workers and apply it in
the staging maintenance step before starting matching processes. No deployment
was performed as part of implementation.

`GET /assistant/workflows` advertises `mail_search` separately from existing
capture-read actions, with supported folders/window/page size. B09a help and read
release remain pinned to their original capability description for queued tasks;
clients should use the current capabilities endpoint for the new search control.

Tests exercise real PostgreSQL owner isolation, literal query handling, date/label
boundaries, unknown metadata, stable pages, cursor expiry/tamper/sync invalidation,
quote integrity, sync-lock fencing, sanitized contention errors and migration parity.
No live Gmail completeness, model inference or frontend/load integration is claimed.
The next B09 work is persistent entity/commitment provenance and corrections, then
bounded natural-language read planning and the B10 compound integration.
