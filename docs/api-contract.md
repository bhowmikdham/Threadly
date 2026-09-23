# API contract — v0 DRAFT

> Current selected-thread behavior: [grounded answers and receipt fixes](grounded-thread-fixes.md).

> Current correction: [on-demand Gmail](on-demand-gmail.md) supersedes the mailbox-sync,
> local-search and stored-source assumptions below. Default runtime fetches selected sources
> from Gmail and stores references only; bulk sync is retired. See that contract before integration.

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
- Auth: `Authorization: Bearer <session JWT>` on protected routes. Auth begin/exchange are public; reconnect, disconnect and session refresh require JWT.
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
small-model configuration, one bounded attempt and strict JSON validation. The intent
router and draft generator accept raw JSON or exactly one complete JSON Markdown
fence; extra prose, multiple objects, duplicate keys and invalid schema/semantics
remain rejected. Model-selected recipients are never accepted. A hint
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
| POST   | `/auth/google/exchange` | `code`, exact `redirect_uri`, one-use `state`, original `code_verifier` from the B01 handshake below | `{"jwt": "...", "user": {"id", "email", "name"}}` |
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

The current policy candidate is `summary-quality-1.0.2`: descriptive summaries,
explicit outstanding source actions only, and no invented advice in overview.
The JSON/artifact schema is unchanged. Case-specific evaluation checks are not a
universal runtime grounding validator. See [master workflow](master-workflow.md)
for supported single operations and the still-planned compound executor.

New requests pin `summary-quality-task-1.0.0` around their native/Flow release (inside
the UI wrapper when present). The summary artifact shape is unchanged. Generation
now has hard budgets: overview 80 words, list items 35 words, 3 decisions, 3 actions,
2 questions and 180 words total. Extra fields, fenced JSON and repeated normalized
fields fail as `invalid_summary_output`; existing source validation still applies.
Historical queued releases retain their old prompt and validator. See
[summary quality and frontend presentation](summary-quality.md).


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
read tasks now pin `bounded-reads-task-1.1.0` (capability-aware help text).
See [the compose routing correction](compose-routing-fix.md) for updated draft/router releases.
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


## Explicit compound templates (B10a)

`POST /assistant/compound-requests` (authenticated, 202) accepts strict
`CompoundRequest` schema 1.0: request ID, owned context snapshot ID, template
(`summary_then_reply` or `summary_then_compose`), `summary_in_draft` boolean,
existing `draft_options` and bounded `draft_instruction`. These are explicit user
selections, not classifier proposals. Unknown operations/keys are rejected before
execution; missing recipients/target fail preflight. The original request endpoint
and historical request hashes are unchanged.

Task responses add nullable `compound`, containing both planned steps, attempts,
state, dependencies, stream/output IDs and completed/total counts. `artifact_id`
remains the final result only after success. Artifact responses add `stream_key`
and `is_final_result`; `is_latest` is relative to the artifact's stream. Draft
history excludes intermediate summaries; edits update the explicit final pointer.
New SSE events: `step.started`, `step.succeeded`, `step.failed`; final-only
`artifact.ready` and `task.finished` remain compatible. Review still cannot send.

`GET /assistant/workflows` adds `compound_templates` with installed templates,
explicit-selection requirement, two-step bound and natural-language planner false.
Examples and recovery: [compound workflows](assistant-compound-workflows.md).
Whole implementation/testing map: [workflow testing](workflow-testing-map.md).

## Action storage integration (B02; internal only)

B02 itself added no action HTTP endpoint; B03 below now exposes proposal/read. Existing draft editing
now supersedes internal `proposed`/`approved` action records for the previous final
artifact in the same transaction. It does not recall `executing`/`outcome_unknown`
actions. Draft review remains an acknowledgement with `authorization: none` and
`sending_available: false`. Legacy `/draft*` routes are not redirected to a sender.

Existing task-event replay can include `action.proposed` with `action_id`,
`artifact_id`, `action_type`, `state`, and `action.state_changed` with `action_id`,
`state`, plus `reason: draft_revised` for edit supersession. The normal task-event
envelope/sequence/version applies; payloads and recipients are excluded. These
events now also arise through B03 proposals and B04 stop decisions; new public approval is disabled.
See [action storage](assistant-action-storage.md) for errors, schemas and lifecycle.

## Google auth/capabilities (B01)

The old stateless exchange input is superseded: missing state/verifier returns 422.
`POST /auth/google/begin` accepts strict `{redirect_uri,code_challenge}`; authenticated
`POST /auth/google/reconnect` accepts that body plus optional named
`capabilities: ["calendar_read"]` and binds the current account.
Both return `{state,authorization_url,expires_at}`. State is one-use, expires after
ten minutes and is consumed before network exchange. `POST /auth/google/exchange`
checks the original verifier, callback and state; the JWT/profile response is unchanged.
Authenticated `POST /auth/google/disconnect` clears local Google credentials only.

Domain-free staging may explicitly enable `GOOGLE_ALLOW_LOOPBACK_TEST_CALLBACK=true`.
Only `http://127.0.0.1:8765/oauth/callback` then accepts HTTP, still requiring exact
redirect allowlisting and the same state/PKCE exchange. Default remains HTTPS-only.
See [local test setup](google-local-testing.md).

`GET /assistant/capabilities` returns typed account/capability/reconnect metadata
from stored actual grants. No user ID or scope authority is accepted from the caller.
Gmail send and Calendar writes remain disabled. B12 installs Calendar list/freebusy
reads; both `calendar_list` and `calendar_read` grants must be ready.
`ready` is stored readiness, not a live provider probe.

Errors: 400 `invalid_redirect_uri`/`oauth_state_invalid`; 401 `oauth_exchange_failed`/
`reauth_required`; 409 `google_account_mismatch`/`google_connection_changed`;
503 `google_not_configured`/`google_token_unavailable`. Errors omit provider bodies.
Read the [full handshake, diagrams and compatibility note](google-capabilities.md)
before implementing a client. Existing Chrome getAuthToken calls do not supply
backend authorization codes; frontend integration remains deferred.

## Exact email previews (B03)

Authenticated `POST /assistant/artifacts/{artifact_id}/actions` accepts strict
`{request_id,expected_revision,action_type:"send_email"}` and returns 201
`EmailActionView`. Authenticated `GET /assistant/actions/{action_id}` returns the
saved owner-only preview with current blockers. Recipients/body come exclusively
from the current edited artifact; arbitrary payload overrides are rejected. The
response exposes From/To/Cc/Bcc, subject/body, threading headers, hashes, versions
and 30-minute expiry, with `authorization: none`, approval/sending false. No raw
MIME is returned. Retries reuse the original candidate; edits supersede it. Both
responses use `Cache-Control: no-store`. B04 adds decision routes below; no send route exists.

[Full schema, blockers, replay and client contract](email-action-previews.md).

## Exact approval and stop decisions (B04)

Authenticated `POST /assistant/actions/{id}/approve` accepts strict
`{request_id,expected_version,payload_hash}`; new public approvals return 409 while
live execution/recovery gates are closed. Internal exact approval/outbox is transactional; matching
existing requests may replay with 202 without requeueing. `/reject` and `/cancel`
accept strict `{request_id,expected_version}` and return 200. Rejection closes a
proposal; cancellation closes pre-dispatch work or records a request after cutoff.
No replacement recipients, body or execution switch is accepted.

Responses contain `{request_id,operation,decision,action}`: the decision is the
original receipt; nested action is current. GET adds `approval_id`, historical
`authorization` (`none` or `exact_payload_approval`), `cancellation_requested` and
`allowed_operations`. Both availability booleans remain false. A late cancellation
never reports not-sent, changes action state or suppresses recovery. New SSE event
`action.cancellation_requested` contains only `action_id`.

[Full contracts, errors, replay, locking and migration](action-approval.md).

## Disabled email action worker (B05)

No new route. `EmailActionView` now includes nullable `result` and `error_code`.
On confirmed Gmail API acceptance, `result` contains `gmail_message_id` and
`gmail_thread_id`; acceptance is not a recipient-delivery receipt. Unknown outcomes
have no success result and must not be described as unsent or automatically retried.
The existing `action.state_changed` event covers executing/terminal/unknown states.
New `action.preflight_blocked`, `action.recovery_required` and `action.late_response`
events carry action IDs plus sanitized code or attempt ID, not message content.
New public approvals, `approval_available` and `sending_available` stay disabled;
legacy send remains 501. [Worker contract and result policy](email-actions.md).


## Read-only email recovery (B06)

`EmailActionView.recovery` is null except for `outcome_unknown`. It contains
`status` (`paused`, `scheduled`, `checking`, `manual_inspection`), integer `rounds`
and `max_rounds=3`, nullable `next_check_at` and `last_code`, and static `guidance`.
An empty Sent search or exhausted read budget never changes unknown to failed.
A unique complete match can set succeeded with Gmail IDs; this is Sent evidence,
not recipient-delivery confirmation. Account changes/stale leases fence publication.

New proposals on a task with an executing/unknown send return 409
`email_preview_blocked`, blocker `previous_send_unresolved`, including after edits.
Existing proposals expose the same blocker at approval/preflight; historical key
replay remains available. There is no resend/budget-reset endpoint; failed actions
require a new preview and independent approval, which remains publicly disabled.
Recovery events and limits: [email actions](email-actions.md).

## Explicit lookup → draft templates (B10b)

`POST /assistant/compound-requests` additionally accepts `lookup_then_reply` and
`lookup_then_compose` under a separate strict schema selected by `template`.
Fields are `schema_version`, `request_id`, `context_snapshot_id`, `template`,
`query` (literal text, 1–200 characters), `draft_options`, `draft_instruction`.
Do not supply `summary_in_draft`, cursor, operations or arbitrary source refs.
Existing summary request/replay contracts are unchanged.

Task `compound` adds `query` for these templates, with requested outputs
`lookup` and `result`; the final draft depends on step 1. No matches or more than
ten matched messages stops before generation with `lookup_no_matches` or
`lookup_scope_too_broad`. The saved lookup remains available as partial output.
`GET /assistant/workflows` advertises both lookup templates, their separate release,
saved-capture scope and ten-match bound. It continues to report no natural-language
planner. Details, source mapping and request example: [lookup/draft](lookup-draft-workflows.md).

## Reviewed compound-command plans (B10c)

| Method | Route | Behavior |
|---|---|---|
| POST | `/assistant/command-plans` | 202; idempotently reserve and interpret a complete command into a saved reviewable plan, no task execution |
| GET | `/assistant/command-plans/{plan_id}` | Owned immutable input/result, state/hash/expiry and optional confirmed task ID |
| POST | `/assistant/command-plans/{plan_id}/confirm` | 202 existing task response; strict `plan_hash` + `confirm_complete_command: true`; recheck and atomically consume into one compound task |

New request schema: `schema_version: "1.0"`, `request_id`, `instruction` (1–4,000
characters), optional `context_snapshot_id` and `draft_options`. The planner supports
only the four installed summary/lookup + draft pairs in this slice. It preserves
clause spans, prohibitions and dependency intent; unsupported whole plans create
no generation jobs. Explicit review is mandatory. Clarification requires a new
proposal with corrected full input; no automatic single-intent fallback or in-place
plan editor is added. Old `/requests` behavior and task formats are unchanged.
`GET /assistant/workflows` adds `command_planner`; automatic dispatch remains false.

[Full examples, states, limits and confirmation semantics](command-planner.md).
This confirmation authorizes read/generation work only, never Gmail/Calendar writes.

## Calendar read foundations (B12)

Authenticated `GET /calendar/calendars`, `GET/PUT /calendar/preferences`,
`POST /calendar/freebusy` (201) and `GET /calendar/freebusy/{evidence_id}` expose
Calendar selection, versioned explicit preferences and short-lived owned busy
coverage. Save requires expected_version (0 initially); query requires
expected_preferences_version and aware start/end, not arbitrary calendar IDs.
Read-only incremental consent is opt-in on authenticated Google reconnect.
Partial/omitted/invalid per-calendar results mean unknown; no slot/free/booking
claim is made. Account/preference changes during network reads prevent publication.
Full schemas, examples, limits, errors and live gate: [Calendar reads](calendar-reads.md).

## Deterministic Calendar slots (B13)

`POST /calendar/slot-requests` (202) accepts a typed date/time query, idempotency
request_id and expected_preferences_version. `GET /calendar/slot-requests/{UUID}`
returns an owned fresh receipt. Direct bounded reads return up to three stable UTC
options/local labels, explicit clarification, zero/fewer options or unknown coverage.
Saved anchors survive correction/retry through anchor_from_request_id; complete
requests and their results are immutable. Five-minute maximum expiry, actual grants,
account/preferences/policy versions and padded evidence coverage are checked.

No reservation or event write occurs. Participant zones are display-only. These
direct reads are also used by the typed assistant scheduling handler below. Full inputs, clarification and
error semantics: [Calendar slots](calendar-slots.md).

## Meeting negotiation state (B14a)

Authenticated `/calendar/negotiations` creation (201), owned GET, immutable offer
POST/GET, explicit selection POST (202)/GET and close POST persist thread-bound
meeting state. Requests use expected versions and idempotency keys; selections
reference stored offer/slot UUIDs, never caller-supplied times. Choosing a time
performs a fresh exact-time B13 check outside database transactions, then fences
publication against source/account/preferences/negotiation changes.

Historical offers/selection receipts expose `usable` and `blockers`; stored
`selected` is never a booking approval. New replies invalidate old choices and
require fresh slot queries before reoffering. Complete routes/examples/status and
recovery contract: [meeting negotiations](meeting-negotiations.md). B14b1 adds typed
assistant continuation/artifacts below; B14b2a adds reviewed command extraction. Generated replies, later-email
proposals and B15 event execution remain uninstalled.

## Typed assistant scheduling (B14b1)

`POST /assistant/scheduling-requests` (202) accepts explicit `check_time` or
`suggest_slots`, saved preference version, optional owned context/message anchor,
and typed constraints. It atomically saves the pinned request/date anchor and a job.
The worker reuses B13 and task lease fences; unresolved date/clock/AM-PM/DST fold
becomes a durable question. Answers use the returned
`POST /assistant/tasks/{task_id}/scheduling-inputs` URL (202), expected version,
question ID, idempotency key and only requested typed fields. Original `/inputs`
and generation release contracts are preserved.

Normal task/history/cancel/events endpoints apply. Task views add `scheduling`
(saved inputs, null for older tasks). Artifacts are `availability` or `schedule_options`
with owned B13 query/slot IDs, uncertainty, assumptions and expiry. Their GET adds
`scheduling_status` with current `usable`, `blockers` and `has_available_options`;
historical content alone is not current availability. A fresh nonempty query can be
explicitly adopted into a B14a offer through the existing negotiation endpoint.

`GET /assistant/workflows` advertises the explicit handler. The separate B14b2a proposal API adds reviewed prose extraction. No automatic
router/compound dispatch, draft, send, approval or booking is added.
[Complete schemas, examples, errors, flow and recovery](assistant-scheduling.md).

## Reviewed scheduling extraction (B14b2a)

`POST /assistant/scheduling-proposals` accepts a self-contained user instruction,
request ID, expected preferences version and optional owned context/message anchor.
It returns a persisted, expiring interpretation without queuing Calendar work.
`GET /assistant/scheduling-proposals/{id}` reads that historical proposal.
`POST /assistant/scheduling-proposals/{id}/confirm` requires its exact
`proposal_hash` and strict boolean `confirm_complete_request: true`; it atomically
returns one existing typed scheduling task and consumes the proposal. Full examples,
constraints, lifecycle, retry/error semantics and safety boundary:
[scheduling extraction](scheduling-extraction.md).

`/assistant/workflows.scheduling.natural_language_extraction` is now `true`, with
separate `extraction` release/entrypoint/review/live-evaluation metadata. The typed
scheduling endpoint still requires explicit constraints; automatic `/requests`
scheduling dispatch and mixed-intent Calendar graphs remain unavailable.


## Combined MVP API additions (17 September 2026)

The [MVP workflow map](mvp-workflow-map.md) is the integration sequence and state
handoff. New routes use existing authenticated ownership, error envelopes and
idempotency rules; typed schemas live under `backend/app/schemas/`.

| Method and route | Request / result |
|---|---|
| POST `/assistant/workflow-proposals` | `CoordinatorRequest`; saved complete interpretation or explicit clarification/unsupported result |
| GET `/assistant/workflow-proposals/{id}` | Owned proposal with exact review hash |
| POST `/assistant/workflow-proposals/{id}/confirm` | `ConfirmCommandPlan`; atomically consume proposal and queue one task |
| POST `/assistant/workflow-requests` | `WorkflowRequest`; explicitly selected supported graph; 202 task |
| POST `/assistant/tasks/{id}/plan-review` | `ReviewPlan`; edit current items or select commitment IDs; new immutable revision |
| POST/GET `/assistant/meeting-response-proposals[/{id}]` | `MeetingResponseRequest`; owned later-message/historical-offer interpretation |
| POST `/assistant/meeting-response-proposals/{id}/confirm` | Exact reviewed proposal; queues fresh exact-time read only |
| POST `/assistant/artifacts/{id}/calendar-actions` | `ProposeCalendarAction`; immutable exact event preview; 201 |
| GET `/assistant/calendar-actions/{id}` | Preview, state, blockers, result and approval availability |
| POST `/assistant/calendar-actions/{id}/approve` | Exact hash/version/request ID; 202 queued action, pilot gate |
| POST `/assistant/calendar-actions/{id}/reject` or `/cancel` | Versioned decision; cancellation after dispatch is a request, not proof of no event |
| GET `/entities?context_snapshot_id=...&type=...` | Owned literal candidates in fresh capture, partial coverage |
| GET `/commitments?context_snapshot_id=...` | Current explicitly selected plan items for captured thread |
| POST `/sync/jobs` | `{ "request_id": "unique-key" }`; 202 durable sync receipt |
| GET `/sync/jobs/{id}` | Owned phase/state/pages, sanitized error and final result |
| GET `/assistant/operational-status` | Owner-only state counts, queue age and rollout controls |

`GET /assistant/tasks/{id}` now includes nullable `workflow` progress/step artifacts.
`GET /assistant/workflows` describes the reviewed coordinator, installed finite graphs,
auxiliary generation adapters and separately approved booking. Configuration discovery
is not proof that remote providers passed acceptance. Existing routes remain compatible;
new clients should use background sync jobs instead of inline `/sync`.

Write capabilities are installed but disabled by default. Reconnect accepts explicit
`gmail_send` and `calendar_write` only for enrolled pilot users. Real execution requires
both the corresponding environment flag and local user allowlist membership; scope and
exact approval checks still apply. The historical always-disabled write-capability
statements above are superseded by this explicit pilot behavior. No frontend origin,
public port, domain or automatic send is enabled by this change.


### Frontend selected-message factual questions (2026-09-23)

UI reference release `ui-context-task-1.1.0` supports bounded factual questions
such as “What is the order total in this email?” against exactly one selected
message, or an explicitly mapped ordinal. It uses the existing grounded-answer
quote validator; every answer must be an exact source span. Multiple references,
ambiguous selection, unavailable text and mixed action requests remain blocked
or require clarification. Explicit summary and verbatim-read behavior is unchanged.
The frontend sends included IDs as `visible_message_ids` in displayed order and
a separately chosen target as the single `selected_message_ids` entry.

The reference contract hash changes with this release. Already completed artifacts
remain readable; pending tasks/questions pinned to the old reference contract must
be resubmitted rather than silently run under different interpretation rules.

### Conversational recipient questions (2026-09-23)

Router release `intent-preview-1.3.1` normalizes only `recipient_email`,
`recipient_address` and `recipients` missing-field labels to the existing
`recipient` precondition. A compose request without an authoritative recipient
envelope now opens the existing typed `recipients` question instead of failing
with `clarification_fields_unavailable`. It never derives an address from a
name or from email content. Already bound recipients clear that precondition;
unknown missing fields and exact saved-action review requirements remain intact.

The prompt and schema are unchanged. Six alias/envelope replays, an unknown-field
and action-review regression, and a PostgreSQL task/worker/question regression
cover the change. The committed synthetic Bedrock receipt was rerun on this router
release (six route checks, summary, reply, present/absent factual answers and two
compose checks); it is not a mailbox-wide or external-write acceptance claim.
Pending jobs pinned to an unavailable older release fail closed and must be
resubmitted. Completed artifacts remain readable.
