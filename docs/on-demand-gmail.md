# On-demand Gmail architecture

> Current conversation layer: [context, lifecycle and limits](contextual-conversation.md).

This corrects the mailbox-replication design. The default is `GMAIL_SOURCE_MODE=on_demand`.
The legacy sync implementation exists only for compatibility tests/explicit rollback;
it is not part of the normal staging deployment. Calendar remains direct bounded reads.

## Data lifecycle

```mermaid
sequenceDiagram
    participant UI as Client
    participant API as API
    participant G as Gmail
    participant DB as PostgreSQL
    participant W as Assistant worker
    participant B as Bedrock
    UI->>API: Selected Gmail thread ID
    API->>G: GET users/me/threads/id (one bounded response)
    G-->>API: Selected thread
    API->>DB: Source IDs, owner/account version, content fingerprint, UI mapping
    API-->>UI: Source reference + transient excerpts
    Note over API: Source text released at request end
    UI->>API: Instruction + source reference
    API->>G: Refetch and verify fingerprint
    API->>DB: Durable task; no source bodies
    W->>DB: Claim task and reference
    W->>G: Refetch selected source outside DB transaction
    W->>W: Verify owner, account version and content fingerprint
    W->>B: Bounded excerpts for this task only
    B-->>W: Proposed summary / plan / draft
    W->>DB: Generated artifact and provenance
    Note over W: Source text released at attempt end
```

No first-login import, full mailbox walk, history cursor or automatic indexing is required.
No source bodies, original subjects, original addresses or reply headers are inserted into
`messages` or stored inside `context_snapshots`. A `threads` row is only a source reference
(Gmail ID, version and latest message ID); its subject/date/classifier fields remain empty.

`context_snapshots.payload` uses `storage=gmail-reference-1.0`, containing owner/account
version, Gmail thread ID, local reference version, content fingerprint and optional UI
message IDs/order. `source_hash` binds the deterministic transient excerpt payload.
Fetched mail is held in a `ContextVar` scoped to one API request/worker attempt, explicitly
reset in `finally`; it is never assigned to ORM payloads or a shared process cache.
A worker restart reconstructs context by reading Gmail again. Changed, missing or
revoked sources stop execution; there is no stored-body fallback.

User instructions, generated summaries/plans/drafts, source citations, review state and
exact approved outgoing payloads remain durable application artifacts. They may naturally
contain information from mail. This is **not** a promise that no email-derived information
is ever persisted. Original source messages are not replicated. Gmail remains the source
of truth. Existing conservative artifact retention continues to apply; it is separate
from source retrieval and never authorizes a mailbox cache.

## Bounds and contracts

| Operation | Behavior |
|---|---|
| `GET /threads?days=7` | Live Gmail message listing, one page of at most 20 details, grouped by thread within that page. `days` is 1–30; UTC day boundary. No persisted results. |
| `GET /threads?days=7&cursor=...` | Explicit next page only. Signed cursor binds owner/account/query and expires after 15 minutes. Threads may recur across pages. |
| `GET /threads/{gmail_id}` | One live thread; returns transient message data and version for UI capture. |
| `POST /assistant/context-snapshots` | Same 1.0/1.1 request contracts; on-demand capture stores references only. |
| `GET /assistant/context-snapshots/{id}` | Refetch source; validate fingerprint before returning transient excerpts. |
| `POST /assistant/mail-search` | Response schema 2.0: explicit dates/folder/phrase; one page of up to 20 live Gmail details, excerpts up to 1,000 characters. |
| Search scope | `all_mail`, `INBOX`, `SENT`; `all_synced` is a deprecated request alias for all non-Spam/Trash Gmail mail in the explicit date window. No local search fallback. |
| Search semantics | Gmail phrase search can match headers. Returned body excerpts are not proof of a literal body match. Raw Gmail operators cannot be injected through quoted search text; quote/escape/control characters are rejected. |
| Selected thread | At most 200 messages / existing 8 MB decoded provider-response limit; too-large sources fail explicitly. No attachment fetch. |
| Model context | At most 50 messages / 12,000 body characters, with omitted/truncated coverage. UI captures preserve explicit order/reference mapping. |
| `POST /sync`, `/sync/jobs`, `GET /sync/jobs/{id}` | HTTP 410 `mailbox_sync_retired` in on-demand mode, including when an old background-sync flag is true. |
| Legacy `/threads/{id}/summary` | HTTP 410 `legacy_summary_retired`; use reference capture + durable assistant request. |

Source-dependent assistant entrypoints prefetch owned references before handlers take DB
locks. `context_data()` can only materialize from the current request's validated memory;
it performs no hidden HTTP. Source-independent commands and polling a task need no mail
read. Subsequent user requests/refreshed workers fetch again. Cancel/reject remains
available when Gmail is unavailable.

Reply recipients remain explicit backend-validated draft options. Original subject and
reply headers are read from the selected live message; only the resulting draft/envelope
and exact approved outgoing payload are retained. API approvals and action workers
revalidate source fingerprints on independent reads before execution. Kill switches,
per-user pilot gates and exact-payload approval remain unchanged. Nothing here grants
send or Calendar write permissions.

## Deployment and rollback

`deploy-app.sh` stops all older workers, including an existing sync worker. It starts only
API, assistant-worker and action-worker. The sync service is behind the explicit
`retired-mailbox-sync` Compose profile. Production preflight requires on-demand mode and
background sync disabled. No schema migration is needed: the source-reference manifest
fits the existing JSONB field; source IDs retain existing ownership foreign keys.

Do not roll back to an older image with the on-demand references still in use: older
workers interpret captures as body snapshots. Pause source-dependent generation and
review compatibility before rollback. Do not restart the retired sync worker.

The accidental live test job was stopped before publication. Audit found 300 staged rows,
zero `messages`, zero `threads`, zero captures and zero assistant tasks. The specific job
and staged records were deleted transactionally after verifying the worker was stopped,
the job was queued/unleased and the owner had no published mail or tasks. Google account
and encrypted credentials were preserved. This is logical database cleanup, not a claim
of forensic erasure from PostgreSQL WAL, disk blocks or historical infrastructure backups.

## Acceptance evidence

`backend/tests/test_on_demand_gmail.py` uses real disposable PostgreSQL and fake Google/model
responses to exercise API → reference capture → independent worker read → artifact.
Assertions include zero stored Message rows, no source text in captures, no open DB
transactions during Google HTTP, owner isolation, changed/deleted-source failure,
reference-only reply generation, bounded search and retired sync endpoints.
Live checks must report counts/status only, never mailbox bodies, credentials or prompts.
Model quality and Google send/Calendar write acceptance remain separate from this change.

References: [Gmail thread read](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/get),
[bounded message list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list).
