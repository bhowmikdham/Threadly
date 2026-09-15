# API contract — v0 DRAFT

The seam between `frontend/` and `backend/`. **Contract-first**: any change to a
path, field, or event shape happens by PR to this file, reviewed by both sides,
before the code changes. The pydantic models in `backend/app/schemas/` mirror this.

Status: v0.1 — W1 endpoints are LIVE: `/auth/google/exchange`, `/auth/refresh`,
`/healthz`, `/readyz`, `/sync`, `/threads`, `/threads/{id}`, `/threads/{id}/summary` (SSE).
Still stubbed (501): `/draft*`, `/entities`, `/commitments`, `/voice/*`.

Inference migration: uncached summaries support `INFERENCE_PROVIDER=bedrock`
([ADR 003](decisions/003-bedrock-migration.md)). The initial Bedrock adapter buffers
and validates the full response before emitting one `token` event, followed by
`done` with provider `bedrock`. Model failure emits terminal `error` with code
`upstream_model_unavailable`; no success event or cache write follows. Existing
cached results may still come from legacy inference.

## Conventions

- Base URL: `https://<DOMAIN>` (dev: `http://localhost:8000`)
- Auth: `Authorization: Bearer <session JWT>` on everything except `/healthz` and `/auth/*`
- Content type: JSON unless stated
- IDs: Gmail thread/message ids are passed as opaque strings

### Error envelope (R18)

Every non-2xx response, no exceptions:

```json
{ "error": { "code": "not_implemented", "message": "human-readable", "detail": null } }
```

`code` is a stable machine string (`unauthorized`, `not_found`, `validation_error`,
`upstream_model_unavailable`, ...). Frontend switches on `code`, never on `message`.

### SSE

Streaming endpoints use `text/event-stream`. Events:

```
event: token        data: {"text": "..."}          # incremental content
event: done         data: {"usage": {...}}          # terminal event, always sent
event: error        data: {"error": {envelope}}     # terminal on failure
```

## Endpoints

### Draft editing and review (implemented in assistant API)

See [the full revision/review contract](assistant-draft-review.md) for request examples,
validation, concurrency, blockers, events and rollout. These APIs use assistant UUID
artifacts; the legacy integer `/draft*` routes remain 501.

| Method/path | Request | Response |
|---|---|---|
| `POST /assistant/tasks/{id}/draft-revisions` | Full subject/body/recipients/unresolved fields, `request_id`, `expected_revision` | 201 saved immutable revision; matching replay returns the same revision; stale distinct edit is 409 |
| `GET /assistant/tasks/{id}/draft-revisions` | `page_size` 1–100, optional `before_revision` | Newest-first revision metadata, latest ID/version and `next_before_revision` |
| `POST /assistant/artifacts/{id}/review` | `expected_revision`, exact `payload_hash` from artifact view | Review acknowledgement of the current unblocked revision, never authorization to send |

Artifact views now include `is_latest`, `latest_artifact_id`, `latest_revision` and
nullable `review` with policy/hash/state/time/blockers/`authorization:none`.
Draft envelopes are revision-specific. Task views reference the latest artifact;
`task.draft_input` continues to describe the original request. Draft edits/reviews
advance task version/event sequence but leave generation state `succeeded`.
`draft.revised` and `draft.reviewed` events contain IDs/status only, no mail text.
An edit makes the prior review stale; a changed locally saved reply source or sender
also invalidates effective review. `sending_available` remains false.

### Assistant intent preview (implemented; no workflow execution)

`POST /assistant/route-preview` requires the session JWT and accepts:

```json
{"instruction": "Summarise this thread", "intent_hint": null}
```

`instruction` is required, nonblank, maximum 8,000 characters. `intent_hint` is
optional and accepts the five canonical intents or null. Unknown fields are
rejected, including user IDs, context IDs and continuation objects. This initial
endpoint reads no mailbox or calendar state and persists no task or approval.

Returns HTTP 200 with `{decision, router_version, source, execution_ready: false}`.
`decision` follows the playbook route-decision contract; `source` is `rule|model`.
An exact summary command returns intent `summarise`, operation `summarise_thread`,
status `needs_clarification`, and missing field `source_context`. A known intent
does not mean an executable workflow exists. The frontend must not dispatch tools
or send messages from this response.

Simple exact commands avoid inference. Richer requests use the selected model's
small-model configuration, one bounded attempt and strict JSON validation. A hint
cannot override a compound request. The initial allowed combinations are summary,
work plan, availability check or slot suggestion followed by reply; entity/mail
lookup followed by reply or compose; commitment lookup followed by reply.
Unsupported or reversed sequences are rejected, not executed.

Errors: 401 missing/invalid session, 422 invalid request, 502
`invalid_route_output` for invalid model proposals, 503
`upstream_model_unavailable` for inference failure. Error details do not expose
raw model output. Request validation detail entries contain `loc`, `type`, `msg`.

The preview does not execute work. Contextual summary execution is available through
the separate durable API below. Existing `/threads/{thread_id}/summary` stays
available; source changes invalidate its cache as described below.

### Durable contextual tasks (summary, reply and compose drafts installed)

Requires migrations through `e9b7120c4a63` and a separately running assistant worker.
All routes require JWT authentication and enforce ownership. Context captures
server-side synced message excerpts; clients cannot upload authoritative mailbox
text, source IDs, user IDs, job state or generated artifacts through these APIs.

| Method/path | Request | Response |
|---|---|---|
| `POST /assistant/context-snapshots` | `{"schema_version":"1.0","thread_id":"<Gmail thread ID>"}` | 201: `context_snapshot_id`, `captured_at`, `source_hash`, scope, captured messages and coverage counts |
| `GET /assistant/context-snapshots/{id}` | — | Owned immutable excerpt snapshot |
| `POST /assistant/requests` | Versioned assistant request below | 202: saved task, state, version, event cursor and URLs |
| `GET /assistant/tasks` | `cursor` (previous page's ID), `page_size` 1–100, optional `state` | `tasks`, `next_cursor`; newest creation time/ID first, owner scoped |
| `GET /assistant/tasks/{id}` | — | Task state, version, latest sequence, snapshot/artifact references, release fingerprint, error code and timestamps |
| `GET /assistant/tasks/{id}/events` | `after` or `Last-Event-ID`, nonnegative sequence | Finite SSE replay batch after that sequence; closes after currently saved events |
| `POST /assistant/tasks/{id}/cancel` | `{"expected_version":1}` | Updated cancelled task; stale version or finished task returns 409 |
| `GET /assistant/artifacts/{id}` | — | `artifact_id`, `task_id`, `revision`, typed `artifact`, model/prompt `provenance` |

```json
{
  "schema_version": "1.0",
  "request_id": "client-generated-unique-key",
  "instruction": "Summarise this thread",
  "intent_hint": "summarise",
  "context_snapshot_id": "<ID returned by snapshot capture>",
  "continuation": null
}
```

`request_id` is 1–128 characters. The unique key is owner + request ID, bound to
the canonical request hash. Identical replay returns the existing task (202 even
if already complete); different input with the same key returns 409
`idempotency_conflict`. Task, initial event and job are committed atomically.

Requests are durably accepted before routing, including free-text and compound
instructions. Context may be null; an explicitly supplied inaccessible snapshot
returns 404 before task creation. Non-null continuations still return 501
`continuation_not_available` and are never reinterpreted as fresh instructions.

The worker classifies using exact commands or the selected small model, validates
and saves its proposal, then dispatches a single summary, reply draft or compose
draft. Summary/reply source context and draft recipients are backend-bound.
Free-text preferences are included in generation. Calendar and broad other execution
workflows are not installed; exact UI-message extraction is available as described below; no supported subset of a compound request runs. A ready route is a proposal, not execution.

Task views include `instruction`, nullable `intent`, nullable `route`, and nullable
`draft_input` with the frozen sender, recipients and reply target. Before
routing, intent/route are null (legacy summary-release tasks retain their original
intent). Route contains `decision`, rule/model `source`, nullable routing model
`provenance`, `router_version`, and contextual `release`. The backend binds its
context ID; the model cannot invent or select source/recipient IDs.

States: `queued → running → succeeded|failed|cancelled|needs_clarification|unsupported`.
All are accepted by the task list state filter. Missing data saves
`needs_clarification` with `route.decision.missing_fields` and `clarification`;
no artifact is created. Show the question and submit the user's fully restated
request/context with a new request ID. These tasks do not resume in place yet.
Unsupported requests use `unsupported_request`; recognized but uninstalled
workflows use `workflow_not_available`, both with state `unsupported`. Invalid
model routes fail with `invalid_route_output`. Full details and examples are in
[the routing handoff](assistant-routing.md).

A transient model failure can return to `queued`. The worker permits at most
three claims total, with short backoff, a 180-second lease and a 120-second timeout
for routing/checkpoint/generation together. Generation retries reuse a saved route.
A cancelled, deleted or expired worker cannot checkpoint a route or publish an
artifact. Cancellation suppresses publication but may not stop an already-issued
model call. Retrying cancellation with the original or current cancelled version
is idempotent. There is no external send/calendar action in this slice.

SSE IDs are persisted task-local sequence numbers. Events are `task.accepted`,
`task.stage_changed`, `task.routed`, `artifact.ready`, and `task.finished`, carrying sequence,
task version, timestamp and payload. Replay returns at most 100 saved events,
with no database transaction held during network streaming. Clients reconnect
using the last seen ID; poll task state with backoff when a batch is empty. Stream
closure alone does not mean the task finished. Browser disconnect never cancels
the task. A cursor ahead of stored state returns 409; malformed/conflicting
cursors return 422. If a history cursor's task was deleted, restart pagination.

Summary artifacts contain overview, decisions, inferred action suggestions, open
questions and backend-resolved evidence IDs. Source-number validation prevents
invented ID references; it does not prove every generated claim is correct.
`coverage` remains `partial` because Gmail sync does not yet guarantee a complete
live view. The snapshot includes at most 50 messages / 12,000 body characters,
records omission/truncation, and persists ordering independently of later sync.
New snapshot payloads also include the captured `thread_version`; historical
snapshots may lack that field. Ordering follows provider receipt time, then the
legacy sent time fallback and a bytewise message ID tie-break. Missing dates sort
first in the oldest-first transcript. See [sync details](gmail-sync.md).
No task result uses the legacy summary cache.

Not yet implemented: task continuation, executable approval
actions, Calendar workflows, context expiry/cleanup, live event following, Flow
invocation or frontend integration. See the
[worker runbook](assistant-worker.md) for startup, recovery and test instructions.

### Auth
| Method | Path                    | Body                    | Returns |
|--------|-------------------------|-------------------------|---------|
| POST   | `/auth/google/exchange` | `{"code": "...", "redirect_uri": "..."}` — auth code from `chrome.identity.launchWebAuthFlow`, plus the redirect URI used | `{"jwt": "...", "user": {"id", "email", "name"}}` |
| POST   | `/auth/refresh`         | — (valid JWT)           | `{"jwt": "..."}` |

### Health
| Method | Path       | Returns |
|--------|------------|---------|
| GET    | `/healthz` | `{"status": "ok", "version": "..."}` — liveness, no auth, no DB touch |
| GET    | `/readyz`  | `{"status": "ok", "postgres": true, "chroma": true}` — 503 + envelope if a dependency is down |

### Sync
| Method | Path    | Body | Returns |
|--------|---------|------|---------|
| POST   | `/sync` | —    | `{"mode": "backfill|incremental", "messages_upserted": n, "threads_touched": n}` — first call walks the whole mailbox (ALL pages), later calls use the Gmail history cursor; expired cursor transparently re-backfills |

Call it right after login, then on side-panel open. Runs inline; duration depends
on mailbox size. Scope excludes SPAM/TRASH. Backfill captures a starting cursor
before scanning and replays all changes before committing. History includes
additions, deletions and label changes. Concurrent syncs use a per-user version
fence; the losing request returns 409 `sync_conflict` and should retry from current
state. No Gmail network request holds a database transaction.

`messages_upserted` counts in-scope detail records processed (including unchanged
replays); `threads_touched` counts threads whose stored content/metadata changed.
Errors: 401 `gmail_reauth_required`, 503 `gmail_sync_unavailable`; provider error
bodies are omitted. Failure does not advance the cursor or partially commit mail.
Migration `3c6e9a1207bd` clears existing cursors to force metadata rehydration on the
next sync. See [sync lifecycle and rollout](gmail-sync.md).

### Threads
| Method | Path                      | Query                                   | Returns |
|--------|---------------------------|-----------------------------------------|---------|
| GET    | `/threads`                | `filter=needs_reply|all`, `page`, `page_size` | `{"threads": [ThreadOut], "next_page": int|null}` |
| GET    | `/threads/{thread_id}`    | —                                       | `ThreadOut` + messages |
| GET    | `/threads/{thread_id}/summary` | `Accept: text/event-stream`        | SSE stream; cached summaries emit one `token` then `done` |

Thread list entries include an additive integer `version`. Detail response is
`{"thread": ThreadOut, "messages": [...]}`. Messages include `gmail_msg_id`,
`from_addr`, `sent_at`, `is_from_user`, `body_clean`, plus nullable `received_at`
and `reply_metadata` (original selected header arrays, parsed address arrays and
labels; [shape](gmail-sync.md#reply-and-sender-metadata)). Treat headers as
untrusted data. Missing metadata on existing rows remains null until backfilled.
The list orders by newest receipt time then thread ID; message order is shared
with context capture. Empty source threads remain addressable so saved task
references survive, but have null head/time/subject.

Thread versions increase for any stored source change, even when the newest
message ID does not change. Such changes invalidate the legacy summary cache.
If a source changes during summary generation, the SSE endpoint emits terminal
`error` with `context_changed`, no `done`, and does not cache the stale result.
The client must show an incomplete result and allow retry.

### Drafting

Draft generation now uses `POST /assistant/requests` with optional `draft_options`:

```json
{
  "schema_version": "1.0",
  "request_id": "compose-001",
  "instruction": "Write an email asking whether the report is ready",
  "intent_hint": "compose",
  "context_snapshot_id": null,
  "continuation": null,
  "draft_options": {"to": ["person@example.test"], "cc": [], "bcc": [], "reply_message_id": null}
}
```

To/Cc/Bcc accept at most 20 distinct ASCII mailbox literals total, normalized to
lowercase; invalid address syntax, display names, duplicates and control characters
return 422. To is required for generation; no address is inferred from natural
language or From/Reply-To headers. A missing recipient becomes a saved clarification.
Reply requires an owned snapshot and an explicitly selected captured message ID.
Its current local thread version must match the snapshot when accepted.

Source binding errors before acceptance: 404 `reply_target_not_found`, 409
`reply_context_required`, `reply_context_changed`, `reply_subject_unavailable`.
Changed recipients/target with the same request ID return 409 `idempotency_conflict`.
Old clients omitting/null `draft_options` retain their original replay hashes.

Successful draft tasks return an immutable revision-1 `kind=draft` artifact.
`GET /assistant/artifacts/{id}` adds `draft_envelope` (null for non-drafts) and
`sending_available: false`. References `to-1`, `cc-1`, `bcc-1` resolve to the
corresponding zero-based list entry in that envelope. Model output cannot change
those lists or reply subject. Context-free compose may have a null snapshot;
compose always has a null thread reference even when using background mail.

Display subject/body, literal recipients and unresolved fields for review. Extra
model fields, invalid source numbers or unsafe text fail with `invalid_draft_output`.
Initial draft generation does not approve, insert, upload, create a Gmail draft
or send. See [full draft contract and examples](assistant-drafts.md).

Legacy `POST /draft` and `POST /draft/{integer_id}/send` still return 501. Existing
integer-ID draft rows are preserved and are not the new UUID assistant artifacts.
Assistant editing and review endpoints are documented above. Executable approval/send
endpoints remain pending.

### Entities & commitments
| Method | Path           | Query                        | Returns |
|--------|----------------|------------------------------|---------|
| GET    | `/entities`    | `type=` (optional)           | `{"entities": [EntityOut]}` — straight from postgres, no model call |
| GET    | `/commitments` | `status=open|done|all`       | `{"commitments": [CommitmentOut]}` |

### Voice
| Method | Path                | Body                  | Returns |
|--------|---------------------|-----------------------|---------|
| POST   | `/voice/transcribe` | multipart audio       | `{"text": "..."}` |
| POST   | `/voice/speak`      | `{"text": "..."}`     | audio stream (`audio/mpeg`) |

Voice keys never reach the extension; the api proxies ElevenLabs (module 9) and
masks PII before any cloud egress.

## Open questions (settle before W2)

- [x] Pagination for `/threads`: page numbers, `page_size` 25 (decided W1 — shout if the panel wants cursors)
- [ ] Does the side panel want thread list deltas pushed (SSE) or is poll-on-open fine?
- [ ] Draft approval flow: does `send` live in backend (`/draft/{id}/send`) or does the
      extension compose via Gmail UI with the draft text? Changes module 3 scope.

## Workflow implementation configuration

`GET /assistant/workflows` (JWT required) describes the installed generation operations
`summarise_thread`, `draft_reply`, `draft_new` and their configured `native` or
`bedrock_flow` implementation. Each reports `installed: true`, `external_actions: false`.
`remote_resources_verified: false` explicitly limits this to configuration; it is not
per-user Google capability or live model readiness. AWS identifiers are not returned.
Invalid registry configuration uses the normal 503 `workflow_configuration_invalid`
error envelope. `/readyz` also reports a `workflow_configuration` check.

With a configured registry, accepted tasks pin their full workflow release; task and
artifact formats and source/draft validation are retained. Changed aliases, incomplete
streams and invalid evidence cannot publish successful artifacts. No send/Calendar
endpoint is added. See [workflow runtime and remaining gates](workflow-runtime.md).

### Saved UI message mapping (capture schema 1.1)

`POST /assistant/context-snapshots` also accepts schema `1.1` with a required
`ui_map`: version `1.0`, surface `gmail_thread`, nonnegative `thread_version`,
offset-aware `captured_at`, 1–50 unique `visible_message_ids` and selected IDs
from that list. The backend hydrates owned synced text; uploaded bodies are rejected.
The response adds the immutable map, capture policy and truncated message IDs.
Old schema `1.0` requests and responses retain their shape and behavior.

`GET /assistant/workflows` adds `ui_context` with capture schema, supported surfaces,
reference handlers, the 50-message limit and `external_actions: false`.
`What's in the third message?` on a mapped snapshot produces a source-linked
`answer` with selection-only coverage using the saved visible order. A mapped
single-message summary uses the configured native/Flow generation with only that
message. Missing/ambiguous selections clarify; unknown reference operations are
unsupported. No continuation or external write is implied.

See [the frontend recipe, JSON examples and error table](ui-context-mapping.md)
for exact language, limits, source handling and deployment compatibility.

### Concise summary generation release

New requests pin `summary-quality-task-1.0.0` around their native/Flow release (inside
the UI wrapper when present). The summary artifact shape is unchanged. Generation
now has hard budgets: overview 80 words, list items 35 words, 3 decisions, 3 actions,
2 questions and 180 words total. Extra fields, fenced JSON and repeated normalized
fields fail as `invalid_summary_output`; existing source validation still applies.
Historical queued releases retain their old prompt and validator. See
[summary quality and frontend presentation](summary-quality.md).
