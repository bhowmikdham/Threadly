# Gmail sync fidelity — T02 implementation

> Current correction: [on-demand Gmail](on-demand-gmail.md) supersedes the mailbox-sync,
> local-search and stored-source assumptions below. Default runtime fetches selected sources
> from Gmail and stores references only; bulk sync is retired. See that contract before integration.

The sync worker supplies deterministic, owner-scoped source data for assistant
workflows. It reads Gmail; it does not send replies, modify Gmail labels, or create
calendar events. The API remains `POST /sync`, executed inline.

## Lifecycle

```mermaid
sequenceDiagram
    participant API as Authenticated API
    participant G as Gmail REST
    participant DB as PostgreSQL
    API->>DB: Read owner, cursor and sync_version
    API->>DB: Close read transaction
    alt Initial sync or expired history cursor
        API->>G: Read profile starting historyId
        API->>G: List all in-scope message pages and fetch details
        API->>G: Read all history pages since starting historyId
        API->>G: Refetch every changed message
    else Incremental sync
        API->>G: Read all history pages since saved historyId
        API->>G: Fetch every changed message
    end
    API->>DB: Lock user row and compare sync_version
    alt Another sync already committed
        DB-->>API: Roll back; return 409 sync_conflict
    else Version still matches
        API->>DB: Reconcile messages, recompute changed thread heads
        API->>DB: Increment changed thread versions; invalidate caches
        API->>DB: Save cursor and next sync_version; commit together
        DB-->>API: Sync counts
    end
```

There is no database transaction held during Gmail requests. Two requests can
fetch concurrently; only one can commit from the same observed version. The
loser must restart from current state. The version fence also works when Gmail's
cursor did not change, and full reconciliation is scoped to the authenticated
owner. A failure before commit leaves the existing mailbox and cursor intact.

The starting cursor is captured before the backfill scan. History replay then
refetches current details for additions, deletions and label changes, including
IDs already fetched during the scan. A message-detail 404 removes that message
from the cache. Other detail errors abort the run. An expired incremental cursor
triggers a full scan; an expired backfill replay cursor fails the request so a
subsequent request can restart with a new starting cursor.

Scope is all messages excluding SPAM/TRASH. Label changes are read so a message
entering or leaving that scope updates the local cache. Successful full scans
also remove cached messages absent from the reconciled result. Empty thread rows
are retained to preserve saved assistant snapshots/tasks; their current head and
subject become null. Historical snapshots are immutable and may retain excerpts
of mail subsequently removed from the local cache. Retention policy work remains
open and must not be confused with live-mail visibility.

## Ordering and versions

All current message ordering uses the same helper, `message_order`:

1. Gmail `internalDate`, stored as UTC `received_at`.
2. If unavailable (including old rows before rehydration), UTC `sent_at`.
3. Equal times use bytewise Gmail message ID order (`COLLATE "C"`). IDs remain
   opaque strings; the tie-break does not infer a time from their numeric value.
4. Missing both dates sorts before dated messages in an oldest-first transcript.

`sent_at` preserves a valid timezone-aware Date header normalized to UTC. Missing,
invalid or unzoned Date headers fall back to `internalDate`; the server's local
zone is never assumed. Original Date text is retained in reply metadata.

Thread head, subject and `last_msg_at` are recomputed from the newest remaining
message after applying the entire batch. Older arrivals cannot replace that head.
A deletion can legitimately reveal an older head. `threads.version` increases
once per sync for any change to that thread's stored messages/metadata, including
a changed older message; identical replay leaves it unchanged. This is a local
content revision, not Gmail's historyId. New snapshots include `thread_version`;
old snapshots remain valid with no fabricated version added.

Ordering agrees between thread details, legacy summary input and new context
snapshots. It is not a mapping to Gmail's current visible layout. Resolving “the
third thread” still requires a separately captured, authorized UI reference map.

## Reply and sender metadata

`reply_metadata` is nullable JSONB with schema version `1.0`:

```json
{
  "schema_version": "1.0",
  "headers": {
    "from": ["Person <person@example.test>"],
    "to": ["Me <me@example.test>"],
    "cc": [],
    "reply-to": [],
    "message-id": ["<message@example.test>"],
    "in-reply-to": ["<parent@example.test>"],
    "references": ["<parent@example.test>"],
    "date": ["Sun, 13 Sep 2026 10:00:00 +1000"]
  },
  "addresses": {
    "from": ["person@example.test"],
    "to": ["me@example.test"],
    "cc": [],
    "reply-to": []
  },
  "label_ids": ["INBOX"]
}
```

Header arrays preserve duplicates and conflicting values for later validation.
Parsed addresses are deduplicated, sorted and casefolded. `is_from_user` requires
one From header whose parsed mailbox set exactly equals the connected account's
email. A matching display name, substring, suffix or unverified alias does not
qualify. This is an identity-matching heuristic, not proof that a sender was
authenticated. The future send service must independently validate recipients,
verified aliases, RFC headers and the exact approved payload. Never concatenate
these untrusted headers into outgoing mail without validation.

Raw MIME/bodies are not stored. Selected original headers are stored as data;
full From headers remain available even if the legacy `from_addr` display column
is truncated at 320 characters. Metadata is exposed only through owner-authorized
thread detail reads and is not added to model prompts by this change.

## Summary cache behavior

Any thread change invalidates its legacy summary cache, even when the newest
message ID stays the same. Legacy generation copies input under a short thread
lock and releases it before inference. Cache publication locks that thread again
and checks the captured version. A changed source emits terminal SSE error
`context_changed`, with no `done` event and no stale cache entry. Previously emitted
tokens can still be visible, so the client must treat the terminal error as an
incomplete result and offer retry.

Durable tasks continue to summarize their saved immutable snapshots, independently
of later sync. Their source hash now includes the captured thread version. This
change does not add model/prompt-aware caching to the legacy endpoint.

## Migration and rollout

Migration `3c6e9a1207bd` follows `8f3a7c2d901b`. Apply it using the same operational
migration procedure as the [assistant worker](assistant-worker.md). It adds two
version columns plus nullable received time, subject and reply metadata; existing
message rows and assistant records are preserved. It clears sync cursors so the
next sync hydrates metadata for old, unchanged messages, and removes regenerable
legacy summary cache rows. Budget for one full backfill per existing account.

An operator should stop old API/sync processes before migrating and run matching
API/worker code afterwards. Old binaries do not honor the new sync/version
protocol. Downgrade removes the added metadata/version columns; it cannot restore
cleared cursor/cache values. No migration or deployment was run against a live
mailbox database during implementation.

Errors use the standard API envelope: `sync_conflict` (409; retry from current
state), `gmail_reauth_required` (401; reconnect), `gmail_sync_unavailable` (503;
retry later). Repeated pagination tokens and invalid/regressing history cursors
abort without checkpointing. Provider error bodies are not returned to clients.
The returned `messages_upserted` counts in-scope detail records processed, even
if unchanged; `threads_touched` counts actual changed thread revisions.

## Verification and limits

`tests/test_gmail_fidelity.py` adds 26 cases around ordering, date fallback,
spoof/alias matching, reply headers, mid-backfill arrivals/deletes, failed replay,
concurrent syncs, expired cursor recovery, owner isolation, summary fencing and
sanitized API errors. Integration cases use real PostgreSQL and simulated Gmail
HTTP responses. `test_assistant_migration.py` covers empty installation, upgrade
with existing mail, downgrade and Alembic model/schema drift. See the
[progress record](implementation-playbook/13-implementation-progress.md) for results.

Still needed before production: live Google test-account verification, large-mailbox
latency/memory measurement and background resumable sync. HTTP detail tasks are
bounded to eight, but all details are still buffered in memory and the final DB
transaction scales with mailbox size. No push subscription, periodic sync job,
attachment download, verified-alias discovery or complete live-mailbox guarantee
is added. Assistant coverage remains partial; Gmail pagination is not an atomic
mailbox snapshot and saved excerpts are bounded/cleaned.

The implementation follows Gmail's [sync/history semantics](https://developers.google.com/workspace/gmail/api/guides/sync),
[history change types and cursors](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list),
and [internalDate ordering field](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages).
Capturing a profile cursor before scanning and using a backend version fence are
Threadly design decisions built around those APIs, not a Google transaction guarantee.
