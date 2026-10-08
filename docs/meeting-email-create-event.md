# Create an event from an email

Implemented locally on the released backend base `bc12ef106659f5ac8b5b79890e0887f1431e29ea`.
The original independent change is now integrated locally with conversation-context
continuity. See [combined review](evaluation/chat-context/integration-review.md).
It is not deployed.

An email-result card offers **Create event**. Clicking it reads that exact Gmail
message through the authenticated account, displays a bounded fresh source excerpt,
and opens an editor. Only the subject becomes a proposed title. Date and start time
are required user entries. Attendees, location and description begin empty. The
timezone and initial duration come from visible saved Calendar preferences.

**Review event preview** creates an immutable proposed Calendar action. It never
creates an approval or dispatch job. The final **Create event** (or **Create event &
send invitations**) click submits the existing exact payload/version/hash approval.
Chat Always permission is deliberately not consulted by this flow. A user entering
attendees must also explicitly select invitation notifications before previewing.

## API

Both new routes are authenticated, use strict input schemas and `Cache-Control:
no-store`, and require `GMAIL_SOURCE_MODE=on_demand`. Legacy stored-mail mode returns
`409 live_mail_required`; there is no saved-body fallback.

`POST /calendar/meeting-email/draft` accepts:

```json
{"source":{"kind":"gmail_message","thread_id":"abc123","message_id":"def456"}}
```

It returns the same typed source identity, an owned `context_snapshot_id`,
`source_subject`, `source_sender`, `source_excerpt` (at most 6,000 characters),
`source_truncated`, `title`, `timezone`, `default_duration_minutes`,
`preferences_version`, writable selected `calendars: [{id,name}]`, and
`confirmation_required: true`. It does not return inferred times or guests.

`POST /calendar/meeting-email/previews` accepts the same `source`, the owned
`context_snapshot_id`, a caller `request_id`, `expected_preferences_version`,
`calendar_id`, `title`, ISO `date`, local `start_time` to the minute,
`duration_minutes` (5–480), optional `location`, `description`, `attendees`, and
explicit `send_updates: "none"|"all"`. Nonempty attendees require `all`. Unknown
properties, source kinds, timezone-bearing clocks and unconfirmed notifications
fail validation. The destination must still be an editable selected Calendar.

It returns the existing Calendar action view with `state: proposed` and
`authorization: separate_exact_event_approval`. Same request/same input returns
the same action; changed input under the same key conflicts. Candidates expire in
10 minutes or at the event start, whichever comes first. New previews require a
capture less than 15 minutes old and a future event ending within 90 days. DST gaps
and folds require another local time; the server does not guess an offset.

Existing `/assistant/calendar-actions/{id}` read/approve/reject/cancel routes
and the Calendar worker handle the remaining lifecycle. Editing first rejects the
old exact preview with its current version, then creates a fresh candidate. A
racing approval blocks that edit. The frontend retains only the action ID under an
account/origin/typed-source key and reopens existing proposed, queued, unknown or
successful status before permitting another preview. A restored proposed preview
can be cancelled and reopened to edit; no editor text is stored in browser storage.

## Ownership, persistence and execution

The source is selected by provider thread/message identity, never a display label,
card ordinal or model quote. Gmail credentials are owner-derived. The selected
message must be a member of the fetched thread. Preview creation verifies the
owned snapshot's exact single-message map. Email instructions cannot supply event
fields, approval mode or tool authority.

The editor stores only an existing reference-only `ContextSnapshot`. The completed
task retains that reference; its generated artifact and action retain the exact
user-reviewed event payload. Action source versions contain the typed source,
capture ID, request digest, account/session/preference versions, and fixed
`approval_mode: ask`. Original email bodies, source excerpts and sender details
are not stored in these new records. There are no schema migrations or model,
prompt, voice, account-setting or provider-write changes.

Existing source-prefetch machinery reloads Gmail outside transactions before
preview, approval and dispatch. Source fingerprint changes, missing/foreign
messages, changed account/session/preferences, expired candidates and changed
artifact payloads fail closed. Google calls occur outside held database locks.
Existing worker ACL/freebusy preflight still runs immediately before dispatch;
the editor does not claim that the proposed interval is free. Exact-ID uncertain
outcome reconciliation remains the existing worker's responsibility.

## Verification and limits

The PostgreSQL tests use the separate disposable `threadly_meeting_email_test`
database on local port 55439, never the context task's database. Gmail and Calendar
transports are fakes; no live model or provider writes are performed.

- New focused tests: **14 passed**. They cover opening/previewing without approvals or jobs even
  under Always, exact approval and one fake insert, idempotent replay/conflict,
  cross-user/message denial, fresh source at preview/approval/dispatch, preview
  retirement before edits, strict fields, legacy-mode refusal, DST gap/fold and
  source instructions that try to add guests or approve themselves.
- Related released Calendar/Gmail/voice regression set: **129 passed**.
- Frontend verification is recorded in its corresponding feature document.

This first version supports one-time, timed events entered from the visible email.
It does not automatically extract dates, resolve ambiguous language, infer guests,
handle all-day/recurring events, or modify an already-created event. Original
source access is required while reviewing an email-grounded candidate. Local
tests do not establish live Google-account compatibility or production readiness.
