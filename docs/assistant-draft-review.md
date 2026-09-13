# Editable drafts and exact review — T10 partial

This slice adds user-authored revisions and review acknowledgements to assistant
UUID draft artifacts. It makes no model call and performs no Gmail/Calendar write.
Review means the user has inspected this exact draft. It is **not approval to send**;
`review.authorization` is always `none` and `sending_available` remains `false`.
The future action service must create its own exact outgoing payload and approval.

## Lifecycle and boundaries

```mermaid
sequenceDiagram
    participant UI as Client editor
    participant API as Assistant API
    participant DB as PostgreSQL
    UI->>API: Get task and latest artifact
    API-->>UI: Text, literal envelope, revision, review hash and blockers
    UI->>API: Save full edit with expected_revision and request_id
    API->>DB: Lock owned task; compare revision and deduplicate request
    API->>DB: Append artifact + envelope; append draft.revised event
    API-->>UI: New unreviewed revision (old revision preserved)
    UI->>API: Acknowledge review of artifact ID + revision + exact hash
    API->>DB: Lock task; check latest revision, hash and local blockers
    API->>DB: Save review acknowledgement + draft.reviewed event
    API-->>UI: reviewed; authorization none; sending unavailable
    Note over UI,DB: A subsequent edit supersedes the review. No write is queued.
```

The UI must display the subject/body, connected sender, all To/Cc/Bcc addresses,
reply target, unresolved fields and review blockers. Label the control **Mark
reviewed**, not Send or Approve send. Do not acknowledge review automatically when
loading a draft. Save local edits before review; acknowledge the hash returned for
that saved revision. A hash is a concurrency/content binding, not an access token.

## Save a revision

`POST /assistant/tasks/{task_id}/draft-revisions` (JWT; 201):

```json
{
  "request_id": "edit-001",
  "expected_revision": 1,
  "subject": "Project update",
  "body": "Could you send the report on Monday?",
  "recipients": {
    "to": ["person@example.test"],
    "cc": [],
    "bcc": []
  },
  "unresolved_fields": []
}
```

This is a full replacement of editable fields, not a patch. All six top-level
fields are required. `request_id` is 1–128 characters, scoped to the task's edits.
`expected_revision` is an integer >=1. Subject is nonblank, at most 998 characters,
and cannot contain controls/newlines. Body is nonblank plain text up to 20,000
characters; only tab/CR/LF controls are allowed. Up to 25 nonblank unresolved field
descriptions are accepted, each <=300 characters. Unknown fields are rejected.

Recipient rules match initial draft input: ASCII literal addresses, normalization,
no duplicates across To/Cc/Bcc and at most 20 total. At least one To is required.
Omitted Cc/Bcc default to empty, so the editor must submit the complete desired
recipient set. `recipients.reply_message_id` may be omitted or null only; the
schema inherits recipient validation but cannot switch the reply target.

Connected sender, reply identity/headers, mode, context, attachments and source IDs
are not editable. Reply subject stays fixed to the original binding; start a new
compose task to change it. Changed reply context needs a fresh capture/request,
not an edit that substitutes another message. No alias/contact lookup is implied.

The task's original `draft_input` remains immutable. **Use the returned artifact's
`draft_envelope` to display edited recipients.** Recipient refs such as `to-1`
always refer to this revision's envelope. Each new UUID artifact includes its own
copy, so reading an old artifact still returns its old recipients and text.

A matching retry returns the same saved revision with 201, even when newer edits
exist. Check `is_latest`/`latest_artifact_id`; do not overwrite a newer UI with a
historical retry response. Reusing the request ID for different normalized input
returns 409 `idempotency_conflict`. Distinct concurrent edits based on the same
revision serialize: one saves, the other gets 409 `revision_conflict`. Reload and
let the user resolve their local edits; never automatically discard or merge them.

User edits get `user_edit` provenance with `parent_artifact_id` and policy
`draft-review-1.0.0`. Their evidence is the submitted user edit. Original model
citations are not carried over as proof of changed claims; follow the parent
artifact for original generation provenance. No model validates edited facts.

## Read and review

`GET /assistant/tasks/{task_id}` now returns the **latest** artifact ID. Task state
stays `succeeded` after editing/review; task version and event sequence advance.
The original task result was generated successfully even if the user is still
editing it. Existing cancellation semantics for finished tasks remain unchanged.

`GET /assistant/tasks/{task_id}/draft-revisions` returns newest-first metadata:

```json
{
  "revisions": [{"artifact_id":"<UUID>","revision":2,"created_at":"<ISO timestamp>"}],
  "latest_artifact_id": "<UUID>",
  "latest_revision": 2,
  "next_before_revision": 2
}
```

Use `page_size` (1–100, default 20) and the returned `before_revision` cursor for
the next page. `next_before_revision` is null when exhausted. Fetch artifact IDs
separately for full text; history doesn't replicate bodies/envelopes in each row.
New edits don't change already-issued revision numbers or older history pages.

`GET /assistant/artifacts/{id}` preserves existing fields and adds:

```json
{
  "is_latest": true,
  "latest_artifact_id": "<UUID>",
  "latest_revision": 2,
  "review": {
    "policy": "draft-review-1.0.0",
    "payload_hash": "<64 lowercase hex characters>",
    "state": "unreviewed",
    "reviewed_at": null,
    "blockers": [],
    "authorization": "none"
  },
  "sending_available": false
}
```

This excerpt supplements `artifact`, `draft_envelope`, `revision`, `provenance`,
`artifact_id` and `task_id`. Non-draft results return null review/envelope.
Review state is `unreviewed`, `reviewed` or `stale`. Historical review timestamps
are preserved even when a review is stale. Historical unreviewed artifacts stay
unreviewed with `revision_superseded` in blockers.

`POST /assistant/artifacts/{id}/review` (JWT; 200):

```json
{
  "expected_revision": 2,
  "payload_hash": "<copy review.payload_hash from the saved artifact>"
}
```

The backend binds its policy version, artifact UUID, revision, complete artifact
payload and envelope into the canonical SHA-256 hash. Never construct the outgoing
mail payload from this hash or treat it as a send approval. Repeated and concurrent
acknowledgements of the same current revision create only one record/event.
A stale revision or mismatched hash cannot acknowledge a new revision. Sending the
same review after an edit returns 409 instead of silently reviewing the new text.

Review blockers are deterministic and based on **local saved state**:

- `unresolved_fields`: nonempty unresolved list or common `[...]` / `{{` placeholders
  in subject/body. Detection is conservative, not complete semantic validation.
- `recipients_required` / `draft_envelope_unavailable`: missing saved input.
- `sender_changed`: connected saved account email differs from the bound sender.
- `reply_context_changed`: locally synced reply message is missing or its thread
  version changed. This also makes an existing review stale on its next read.
- `reply_headers_unavailable`: the original binding has no usable RFC Message-ID.
- `revision_superseded`: another revision is now current.

The user explicitly edits the unresolved list after resolving facts. Clearing that
list cannot bypass detectable placeholders or the independent missing-header
check. Attachment claims, promises, factual truth and all placeholder styles
cannot be proven by these validators. Review does not certify those claims.
There is no Google refresh, calendar lookup or guaranteed live source freshness
here. The future sender must independently revalidate identity, scope, latest
revision, MIME/header construction and relevant external source state.

## Errors and events

All errors use the existing `{error:{code,message,detail}}` envelope.

| Status/code | Client behavior |
|---|---|
| 401 `unauthorized` | Restore authentication |
| 404 `not_found` | Unknown or other user's task/artifact; reveal no existence |
| 422 `validation_error` / `recipients_required` | Correct fields without losing local edits |
| 409 `draft_required` | Summary or unfinished task cannot use draft APIs |
| 409 `draft_envelope_unavailable` | No usable saved envelope; generate a fresh draft |
| 409 `revision_conflict` | Reload current revision and reconcile editor changes |
| 409 `idempotency_conflict` | Use a fresh request ID for different input |
| 409 `reply_subject_fixed` | Retain reply subject or compose a new email |
| 409 `review_payload_changed` | Reload exact artifact and show it before review |
| 409 `draft_review_blocked` | Get artifact for current blockers; resolve/re-capture |

`draft.revised` emits artifact ID, revision, superseded ID and `review_state`.
`draft.reviewed` emits artifact ID, revision and `authorization:none`. Neither
contains text or addresses. Both use existing task-event replay and task locking.
Clients must continue polling/replaying for editor changes after generation's
`task.finished` event. There is no new streaming subscription or external action.

## Migration and rollout

Migration `e9b7120c4a63` follows `c6e0419a72df`. It replaces the one-artifact-per-task
constraint with unique `(task_id,revision)`, adds owned artifact uniqueness and
edit request keys, copies initial draft envelopes from their tasks, and creates
`draft_reviews` with an owner-enforcing artifact FK. Initial generation remains
revision 1 and unique per task; worker lease fencing is unchanged. Summaries
remain revision 1; the edit API only accepts drafts. Legacy integer `drafts` rows
are untouched and have no automatic mapping to assistant UUID revisions.

Stop old API/workers before applying the migration, then run matching code.
An old worker does not know how to populate revision envelopes; an old reader
might select an arbitrary revision. Review/mutation transactions are short DB-only
transactions; no lock/connection is held while a human reviews or a model runs.
Account/task deletion cascades revisions/reviews. Retention limits are pending.
Downgrade refuses while **any revision >1 or any review record** exists. Do not
remove user work to force rollback; preserve/export it and fix forward.

Verification: see [implementation evidence](implementation-playbook/13-implementation-progress.md).
No prompts or model selection changed, so this slice requires no new model-quality
claim. Synthetic generation plus real PostgreSQL covers the integration boundary.
