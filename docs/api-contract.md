# API contract — v0 DRAFT

## Email badge classification (deployed; disabled by default in fresh configuration)

`POST /threads/{gmail_thread_id}/classification`, with an access JWT and
`{"time_zone":"Australia/Melbourne"}`, returns the current live-thread classification.
Schema `email-classification-with-action.v1` has independent binary `needs_reply`,
native BERT category/priority/action labels, evidence message IDs, fingerprint,
release digest and display expiry. 200 states are classified, needs_review or skipped;
the latter two have null labels. Non-2xx responses use the existing error envelope.
The endpoint fetches mail itself, rechecks source/account/session after inference,
and stores no results or mail. No mailbox import or migration is needed.
See the [frontend contract, examples and error rules](classification/README.md).
Existing `/threads` responses and filters are unchanged; the new endpoint does
not populate the legacy `needs_reply` database column. The same contract works
with the selected Claude Haiku 4.5 classification Flow; model configuration is
backend-only. Nova Micro is retired. See [Flow setup and evidence](classification/VISUAL-FLOWS.md).
The public backend enabled this service on 7 October 2026 after bounded live
integration checks. See [deployment evidence and quality limits](classification/ROLLOUT-2026-10-07.md).
Frontend request scheduling and badge rendering belong to the frontend team.

Conversation release 1.7.0 preserves direct Calendar creation and extends internal
`read_email.scope` and
`prepare_workflow.source_scope` with `thread` (new default), alongside explicit
`selected_message` and `visible_thread`. `prepare_workflow.context_references`
accepts up to four distinct already-read supporting handles, separate from the
primary reference/reply target. Existing HTTP request shapes remain unchanged.
Responses/history add stable `context_references` handles. Context-plan reads
materialize schema 1.2 with message/thread groups and per-source coverage, after
owner/account/fingerprint checks. Clients cannot upload an authoritative plan.
See [shared context](shared-mail-context.md) for bounds and compatibility.

> Current conversation layer: [context, lifecycle and limits](contextual-conversation.md).

> Current inbox chat: [conversational search and result cards](inbox-chat.md).

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
- Auth: `Authorization: Bearer <session JWT>` on protected routes. Auth begin/exchange are public; reconnect and disconnect require an access JWT; session refresh/logout also accept the scoped renewal JWT.
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
store server-issued Gmail references in on-demand mode. The response may include
transient, freshly refetched excerpts, but source body/subject/address data is not
written to the snapshot row. Clients cannot upload authoritative mailbox text,
user IDs, job state or generated artifacts through these APIs.

| Method/path | Request | Response |
|---|---|---|
| `POST /assistant/context-snapshots` | `{"schema_version":"1.0","thread_id":"<Gmail thread ID>"}` | 201: `context_snapshot_id`, `captured_at`, `source_hash`, transient refetched scope/messages/coverage; database payload is reference-only in on-demand mode |
| `GET /assistant/context-snapshots/{id}` | — | Refetches the owned Gmail reference, verifies account/fingerprint/hash, then returns transient excerpts; no stored-body fallback |
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
| POST   | `/auth/refresh`         | — (valid access or renewal JWT) | `{"jwt":"...","refresh_token":"..."}` |
| POST   | `/auth/logout`          | — (valid access or renewal JWT)           | `{"signed_out":true,"scope":"all_sessions"}`; Google remains connected |

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
masks supported email/phone/card-like identifiers before cloud egress. This seed
masker is not full anonymisation.

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
Authenticated `POST /auth/google/disconnect` clears local Google credentials and
invalidates the current Threadly session generation. The response remains
`{connected:false,provider_revocation:"not_requested"}`; Google grant revocation is
not attempted. Protected routes and `/auth/refresh` return 401 `reauth_required`
for that bearer after disconnect. JWTs issued before the generation claim was
introduced also require one fresh Google login after deployment. New bearers are
bound to the connected user's `google_account_version` and
`threadly_session_version`; refresh rechecks both under the user row lock before
returning a new JWT. `POST /auth/logout` advances only the Threadly generation,
invalidating all current devices without removing Google access. See
[Google lifecycle](google-capabilities.md).

`POST /auth/google/begin`, `/auth/google/reconnect` and
`/auth/google/exchange` count every bounded attempt (including schema-invalid
requests) in PostgreSQL before creating state, consuming state or calling Google.
Begin and reconnect share the one-minute begin allowance per observed peer
(default 20); exchange has its own allowance (default 30). Each allowance has a
300-attempt global window. At most 8 KiB of request body is accepted on `/auth`
routes, including streamed requests. A 413 `request_too_large` or 429
`oauth_rate_limited` retains the error envelope; 429 includes `Retry-After` seconds.
If the shared limiter cannot reach its table, sign-in fails closed with 503
`oauth_unavailable`. Raw browser-supplied `X-Forwarded-For` is ignored; the public
ingress overwrites it and Uvicorn accepts it only from the pinned Caddy address.

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
Evidence retains the exact requested instants, including fractional seconds.
The provider transport may query less than one additional second at either edge;
that padding is removed before reporting busy intervals. Echo validation remains exact.
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

Write capabilities are installed but disabled by default. The default rollout permits
`gmail_send` and `calendar_write` only for enrolled `WRITE_PILOT_USER_IDS` users.
`CALENDAR_PUBLIC_ROLLOUT_ENABLED=true`, after separate rollout authorization, makes
Calendar consent and creation eligibility available to current and future signed-in
users without enrolling them for Gmail sending. It defaults to false and does not
change the stored Google grants. Calendar execution still requires
`CALENDAR_WRITES_ENABLED`, a verified connection, actual write scope, editable Calendar
access and the existing exact approval / chat-scoped authorization checks. Reconnect
requests containing Gmail sending still require Gmail pilot membership, even when
Calendar is public. API capabilities, OAuth initiation, job selection, dispatch and
owner-scoped operational status use the same Calendar policy. Neither flag approves an
event or changes the default Ask mode. Public web hosting and Google OAuth publication
do not set this application rollout control.

For an eligible account without the Google write grant, `calendar_write` is enabled
with status `scope_missing` (or `scope_unknown`); the existing extension offers
**Enable event creation** and requests consent explicitly. Excluded accounts remain
disabled, and already-ready accounts do not need another consent button. The historical
always-disabled write-capability statements above are superseded by these controls.


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

## Conversational inbox discovery (on demand)

`POST /assistant/inbox-chat` accepts `{instruction, timezone}` under the session JWT.
The current interpreter release is `inbox-chat-1.2.0`. It returns `release` and one of
`kind: message` with `text`, `kind: continue`, or `kind: search` with `search`.
For “Find emails from person@example.com”, `search.filters` has an empty `query` and
`sender_email: "person@example.com"`. The sender must be an exact address explicitly
written after “from” or “sent by” in the user request. The backend generates Gmail's
`from:` constraint and checks each returned parsed From address. Other literal words
can form a separate quoted phrase query. Search results are live, transient and
limited to one result page of at most five messages; the interpreter uses `limit: 5`.
The backend may inspect up to five provider pages (25 candidate details maximum)
to fill that result page after local checks reject a hit.

`POST /assistant/inbox-search-page` accepts `{filters, cursor}` with the exact filters
returned by the first page. Filters contain `schema_version: "1.0"`, `query` (possibly
empty), `sender_email` (possibly empty), `folder: all_mail|INBOX|SENT`, UTC
`received_from`/`received_before`, `limit` (1–5) and `cursor: null`. The response is
`{filters, results, next_cursor, coverage}`; each result has owned Gmail message/thread
IDs, subject, sender, received time, snippet and optional literal flight preview.
The cursor binds the user, account version, full search scope, release and page size.
Changing the sender, limit or other filters requires a new search. Both routes use
the existing error envelope and perform no mail write. The
[v2 receipt](evaluation/inbox-chat-live-v2.json) records nine passing synthetic Bedrock
cases for `inbox-chat-1.1.0`; the [v1 receipt](evaluation/inbox-chat-live-v1.json)
belongs to release `1.0.0`.

## Contextual conversation API (feature-gated)

Conversation release `1.8.5` adds `search_mail.inbox_category` (`primary` by
 default for Inbox; explicit `all` includes every Inbox category). Public search filters
 default to `all` for old clients. Primary uses Gmail `in:inbox category:primary`;
 category is signed with pagination and must not be inferred from Personal labels.
 For latest-message requests without explicit date wording, optional `search.today_check`
 and the tool observation report `no_messages`, `has_messages`, or `unknown` with
 exact local-day bounds, timezone and query/sender/folder/category. Only exhausted,
 empty provider results prove no arrivals today. Latest cards and their cursor retain
 their original wider date window. Provider errors/timeouts yield unknown; authorization
 and account-generation errors still stop. See [verification](evaluation/primary-inbox/README.md).

`POST /assistant/conversation-turns`: `{conversation_id, request_id, expected_version,
instruction, timezone, context_snapshot_id?, active_task_id?}`. IDs are client UUIDs;
new conversations use expected version 0. Omitted source preserves the pin; explicit null
clears it. Explicit null `active_task_id` clears prior task context. All references are
checked for current-user ownership. No client transcript is accepted.

The 200 response always includes `conversation_id`, the incremented `version`, `kind`,
`text` and `latency_ms`. Normal model-driven completion also includes `release` and
`trace:[{tool,status,reason?}]`; `reason` is a safe machine code on rejected
source-based `respond` attempts (coverage or citation validation), never a quote or
provider response. Recovery from a previously checkpointed durable task/proposal may
return the durable reference without replaying model trace metadata. Optional result fields are:

| Field | Shape and meaning |
|---|---|
| `evidence` | Array of `{reference,quote}` exact-source citations on grounded message/recommendation answers; absent on task/proposal results. |
| `search` | Transient current-turn cards: each Gmail page contributes up to the requested `limit` of 1–5 `results` (`message_id`, `thread_id`, `subject`, `sender`, `received_at`, `snippet`, optional literal `flight` preview). If the model reads multiple pages in one turn, the response aggregates their cards in addressable `mail-N` order, up to 25. `filters` includes the exact `sender_email` and `limit`; `next_cursor` and incomplete `coverage` describe the last page. A new search resets the aggregate. Cards are not replayed from conversation storage. |
| `task_id`, `task` | Existing durable task ID and full task view (`state`, optimistic `version`, question/artifact/event references, release and timestamps). Poll through the existing task APIs. |
| `proposal_id`, `proposal` | Existing command-plan ID and full reviewable proposal. Confirmation is a separate typed endpoint and is not implied by chat. |
| `artifacts` | Immediate full artifact views when the turn creates a draft revision. The durable task's `artifact_id` is authoritative for later fetch/retry. |
| `notice` | Recovery note, currently used when an idempotent replay cannot restore transient search cards. |

`kind` is message, recommendation, clarification, task or proposal. Task/proposal IDs reuse
existing polling, typed inputs, review and confirmation endpoints. Neither the request nor
response is execution approval. Ordinary follow-up text uses another turn with the returned
version. A retry must reuse identical request ID and the complete input, including whether
optional source/task fields were omitted or explicitly null. Busy/stale versions return 409.

In `contextual-conversation-1.8.3`, `search_mail` additionally accepts
`selection: latest_message | recent_matches` (default `recent_matches`). A singular
newest-email request uses `latest_message`, which resolves `limit` to one in the
backend. Plural searches retain `limit` 1–5. `filters.timezone` is the validated
request IANA zone and is preserved and cursor-bound when paging. Each search result
and conversation read adds `received_at_local`, `received_timezone`,
`received_at_display` and `timestamp_source: gmail.internalDate`; authoritative
`received_at` is unchanged. Display snippets are cleaned separately from source bodies
and evidence. Coverage explicitly reports page-local received-time ordering and remains
incomplete; the default date scope is still a rolling year. Historical cursors from
before the additive timezone field must restart their search. No migration is needed.
The [versioned prompt/tools](evaluation/inbox-presentation/contextual-conversation-1.8.3.json)
and [verification checkpoint](backend-execution/checkpoints/latest-inbox-presentation.md)
record the release and test limits.

In `contextual-conversation-1.2.3`, the model's `search_mail` tool accepts
`{query, sender_email?, date_phrase?, folder?, limit?}`; `sender_email` defaults to empty
and `limit` defaults to 5, with 1–5 allowed. An explicit new “from
person@example.com” request starts a fresh exact-sender search with an empty phrase
unless the latest user turn supplied separate search words. “Latest 2 emails in my
inbox” starts a fresh search with `query: ""`, `sender_email: ""`, `folder: "INBOX"`
and `limit: 2`; N may be 1–5. The backend resets old `mail-N` results and the old
cursor for these requests, enforces the current scope before an answer and leaves an
independently pinned email available. A `more_mail` call pages only the current search.
The [v6 receipt](evaluation/contextual-conversation-live-v6.json) records 40/40 passing
synthetic Bedrock checks for release `1.1.6`; the
[v5 receipt](evaluation/contextual-conversation-live-v5.json) covers release `1.1.5`.
Release `1.2.2` combines these search constraints with Calendar agenda support.
Its [deterministic receipt](evaluation/contextual-conversation-merge-deterministic-v1.json)
pins the combined assets; live Bedrock replay for this combined version is pending.

For `prepare_workflow(intent=summarise, reference=selected|mail-N)`, the backend requires
that reference to have been read and creates a typed `operations:["summary"]` task with
the user-authored instruction. `source_scope` defaults to `selected_message`; its saved
source contains only that message, even when the original UI capture displayed others.
`source_scope=visible_thread` requires an owned pinned `selected` context and a prior
`read_email(scope=visible_thread)`; it uses the captured visible messages, never a searched
result's entire Gmail thread. An older full-thread capture without a UI map has thread scope
by default. Compound work must retain one source scope for all requested operations.
Task creation remains idempotent with the conversation turn request ID; it does not send mail.
Source-linked compose with a user-authored `to` recipient also uses the typed `draft_new`
workflow over that bound source. Missing-recipient and source-free compose requests keep
their existing task routing behavior.
For a pending compose clarification, the backend reconstructs the original purpose
and subsequent user answers from the bounded conversation history before submitting
the task. Only addresses explicitly given as To/Cc/Bcc or as an answer to a recipient
question become recipient handles; each handle can be used only in its stated role.
Unrelated prior addresses and addresses merely mentioned in the email topic cannot
be selected. Cancellation or an independent new request clears the pending goal.
A basic draft can proceed with one confirmed To recipient even when `gmail_send`
is disabled. Drafting still creates only
an unreviewed task/artifact; sending remains a separate approval and execution flow.

`GET /assistant/conversations/{id}` returns `{conversation_id,version,history,expires_at,
active_task_id,active_proposal_id,proposal,pending_request_id,pending_recovery,calendar_choices,context_snapshot_id}`. Each
history item is `{user,assistant,kind,task_id,proposal_id,request_id}`; older rows may lack
the newer identifiers. It does not include model tool transcripts or search cards.
`DELETE` returns `{deleted:true,tasks_retained:true}` and is owner-scoped and idempotent;
existing tasks and action records are retained. Deletion returns 409 `conversation_busy`
while that conversation has an active turn lease. Requires bearer auth. Conversation turn
processing requires `CONVERSATION_ENABLED=true`; otherwise 503 `conversation_disabled`.

`read_search_results(references:["mail-1",...])` can inspect one to five distinct,
currently displayed search references in one tool call. It returns selected-message
excerpts capped at 2,000 body characters per reference; only returned text can support
answer citations. Pinned `selected` references and stale/search-invented references are
rejected. When a search has produced cards but the finite tool budget is reached, the
turn completes with those cards and a limited-coverage message, without asserting an
unverified answer. The fallback may carry a citation from a rejected answer only when
its reference and exact quote were already verified against a read observation; a new
search clears those retained citations, and only references from the current
search cards can appear in that fallback. Search-card snippets alone never become
answer evidence. Rejected `respond` attempts receive their specific coverage or
citation feedback even when the model repeats the same answer; each still counts
against the finite turn budget. For a request asking for the latest result, an
answer citing an older searched message is returned to the model if newer cards
remain unread, regardless of whether the answer explicitly claims a rank. The
model can batch-read those references or ask for clarification. A follow-up that
claims a rank receives the same check; any concrete inbox-rank claim without a
verified current-turn citation is rejected, even when only retained search
references remain. Current-turn checks compare returned
timestamps with read state; follow-ups use retained displayed order because card
text and timestamps are not stored. Neither check classifies unread mail. An
uncited clarification must be a direct question or simple request for details;
it cannot assert what the mailbox contains.

All failures use `{"error":{"code":"<machine code>","message":"<safe text>","detail":null}}`
(validation failures use a sanitized `detail` array). Relevant stable codes include
422 `conversation_tool_limit`/`conversation_limit`, 404 `conversation_not_found`, 409
`conversation_version_conflict`, `conversation_busy`, `conversation_retry_required`,
`conversation_account_changed`, 429 `conversation_capacity`/`conversation_history_limit`/
`conversation_turn_limit`, and 503 `conversation_disabled`,
`conversation_provider_unavailable` or `conversation_unavailable`. The public provider code
is generic; SDK exception text, Bedrock error type and raw email bodies are not returned.

## On-demand Calendar agenda and per-account consent (2026-09-28)

Google sign-in creates or resumes a Threadly user from the verified Google subject;
there is no application email allowlist. A signed-in user's
`POST /auth/google/reconnect` may request `calendar_events_read` in addition to
the existing Calendar free/busy grant. The granted scope and owner-bound account
version, not the requested scope, determine readiness. The OAuth account chooser
is shown at sign-in; reconnect still refuses a different Google subject.

Authenticated `GET /calendar/agenda?period=today|tomorrow|this_week|next_7_days`
reads only the owner's explicitly selected calendars in a bounded time window.
The result has per-calendar `known`, `partial` or `unknown` coverage and at most
50 event previews. Private event titles/locations are redacted. No background
Calendar import or event cache is introduced. `read_calendar` can provide a
deterministic agenda answer in `/assistant/conversation-turns`, while the existing
reviewed scheduling/slot/booking routes remain the authority for availability
and external actions. See [the detailed agenda contract](calendar-agenda.md).

## Direct day availability in conversation (2026-09-29)

Conversation release `contextual-conversation-1.2.4` checks standalone self-availability
questions such as “Am I free Thursday this week?” directly through the existing owned
Calendar free/busy service. This read runs before model inference and requires neither
an email source nor a meeting duration. The backend resolves today, tomorrow, ISO dates,
and weekday names against the saved Calendar timezone; “this week” uses Monday–Sunday.
A bare weekday means its next occurrence, including today. A bare confirmation after the
question retains the user-authored request, without trusting an assistant's proposed date.
The common `thurday` typo is supported. Compound requests, specific clock times, ambiguous
“next Thursday”, other people's availability and slot offers retain the coordinator path.

The response uses the existing `kind=message`/`text` contract and adds ephemeral
`calendar_availability` metadata: `date`, `timezone`, `start`, `end`, `coverage`,
`checked_at`, `expires_at`, and owned `evidence_id`. A complete empty free/busy read is
reported as “no busy time recorded on your selected calendars”; partial/unknown coverage
never establishes availability. Busy periods are merged and clipped to the checked day.
Today covers only the remaining hours and says so. Past/invalid dates produce clarification.
Missing access or preferences produce actionable recovery text with a safe `error_code`.
Existing account, scope, ACL, preference version and evidence-expiry fences still apply.

Conversation history retains the displayed answer, but raw availability metadata is not
copied into turn receipts; previous Calendar answers are withheld from model history as
fresh evidence. The free/busy evidence uses the existing storage/expiry policy. No new
endpoint, migration, approval or external-write capability is introduced.


## Availability request recovery (2026-09-29)

Conversation release `contextual-conversation-1.2.5` / policy
`calendar-day-answer-1.1.0` adds the argument-free terminal read tool
`check_day_availability`. The semantic assistant can use it for standalone
whole-day self-availability paraphrases. The backend extracts a single supported
day only from user-authored dialogue, resolves it in saved Calendar timezone,
and rejects unsupported date/time qualifiers and compound actions. Models cannot
supply calendar IDs or dates. Common imperative wording also takes the direct
read path, including both requests reported in the screenshot.

When some selected calendars fail, replies retain verified busy periods and
explicitly name unchecked calendars when current list metadata is available.
Incomplete coverage never supports a claim of being free. Calendar selections
are not silently changed. The additive nullable `display_name` field in each
free/busy calendar result is provider data, not instruction authority. Existing
stored evidence without names remains readable; no migration is required.

Failed and expired workflow proposals use status-appropriate conversation text,
including when hydrating an interrupted turn. They do not claim work is ready
for review. These changes do not grant permission to send or book.

### Conversational Calendar read tools (release 1.3.0)

`POST /assistant/conversation-turns` now exposes five additional model-selected,
read-only tools. They return normal `kind: message` or a specific clarification,
without a workflow proposal. Existing agenda/day tools and HTTP routes retain
compatibility. No Calendar write is added or enabled.

| Tool | Input | Scope |
| --- | --- | --- |
| `list_calendars` | `{}` | Calendar list on the authenticated user's connected account |
| `search_calendar_events` | Date window, optional literal `query` | Existing events on saved selected calendars |
| `find_busy_times` | Date window | Merged known busy intervals; unknown calendars explicitly reported |
| `find_free_times` | Date window, optional literal `duration_phrase`, `limit` 1–3 | Free slots under saved working hours, buffers, notice and duration |
| `find_overlapping_events` | Date window | Overlapping returned event entries, including all-day events; adjacency excluded |

Date-window inputs are `date_phrase` and optional paired `start_time`/`end_time`.
Every supplied phrase must appear in user-authored request context. Supported dates:
today/tomorrow; bare, this or next weekday; this/next week; next 7 days; ISO date;
or inclusive ISO-date range joined by `to`/`through`, at most 14 days. Bare weekdays
mean the next occurrence; explicit next weekday means the following local
Monday–Sunday week. Answers display resolved dates and the saved Calendar timezone.
Clock windows apply to a single day and require unambiguous AM/PM or 24-hour HH:mm.
DST gaps/folds, omitted temporal qualifiers and conflicting ranges clarify.
Event reads allow the past 31 days through the next 90 days; availability reads
require a future portion of the window. Freebusy buffer padding must also fit the
existing 90-day provider bound. These tools do not check participants or rooms.

For example, `find_free_times` with
`{"date_phrase":"Thursday","duration_phrase":"half an hour"}` for the user request
“Find half an hour Thursday” uses backend date/interval calculations and returns
up to three 30-minute options. The times are not reserved or bookable action IDs.
A subsequent booking requires the existing separate scheduling/approval flow.

All Calendar read tools are terminal for a conversational turn: backend-rendered
answers prevent provider event text from causing subsequent model tool execution.
Searches use Google's `events.list` `q` parameter with source-bound literal terms;
its match may be in title, description, location or participant fields. No arbitrary
calendar ID, provider URL, write field or approval is accepted from the model.
One page per selected calendar (25 events), 50 events overall, and 10 displayed
events/overlaps/busy intervals bound results. Pagination/truncation is explicit
partial coverage and asks for a narrower window. Private event details remain
redacted. Overlapping entries can include copies of one meeting, so are labelled
observations rather than guaranteed booking conflicts. Unknown coverage prevents
free-slot suggestions; verified busy periods remain useful and visible.

Transient `calendar_tools` metadata includes operation/check time, coverage, and
freebusy evidence ID/expiry when available. It is omitted from persisted receipts;
user-visible text remains in encrypted conversation history. Calendar-tagged
history is withheld from subsequent model context and must be reread for fresh
facts. Tool failures carry existing `error_code` values and never claim success.


### Semantic Calendar dates (conversation 1.4.0)

The coordinator now interprets date language and calls Calendar tools with a structured
`date` and literal `date_source`. Example: user “am i free tmrw?” →
`check_day_availability({"subject":"self","date":{"kind":"relative","offset_days":1},"date_source":"tmrw"})`.
Relative dates accept offset -31..90 and 1..14 days; weekdays use Monday=0..Sunday=6
and `week: upcoming|this|next`; week queries use `this|next`; absolute dates use ISO
`start` and optional inclusive `end`. Existing horizon and whole-day limits still apply.
`date_phrase` remains accepted only for legacy literal calls and cannot be combined
with `date`. Structured calls require a quoted user date source. The model interprets
meaning; the backend computes dates/DST boundaries, reads only owned selected calendars,
and reports actual coverage. Source quotation does not prove semantic interpretation
correct: this is separately evaluated with live model replays.

Conversation execution no longer routes user wording through the day-question regex
fast path. The day tool takes the same date contract (no clock windows). Server-side
scope checks still reject fabricated source quotes, dropped explicit clocks/qualifiers,
invalid dates, excessive ranges and write fields. They are validation, not intent routing.
No Google permission, endpoint, database schema or external-write enablement changes.

Clock windows also separate literal `start_time_source`/`end_time_source` from their
normalized `start_time`/`end_time`. Equivalent numeric clocks such as “2 pm”,
“2:00 PM” and “14:00” may agree without string identity; a changed clock instant
is rejected. Source fields are optional for legacy calls that copied clocks literally.

Structured Calendar calls also require `subject: self|other`. An `other` subject returns
an unsupported-access explanation before preference or provider reads. Provider account
selection remains server-owned; arbitrary people/calendar IDs are never accepted.


### Calendar settings recovery (October 2026)

`GET/PUT /calendar/preferences` now add `needs_review: boolean`. GET preserves saved
selections for review while flagging an outdated account or policy version. A read with
such preferences returns 409 `calendar_preferences_stale`; explicit versioned PUT is
required to revalidate them. Ordinary concurrent changes retain `calendar_context_changed`.
The whole-day availability tool retries that concurrent-change error once from a fresh
preference snapshot, re-resolving the date/timezone. No write is retried.

An incomplete day response includes `error_code: calendar_coverage_incomplete` alongside
its text and transient evidence. The extension uses this code for “Review calendars”.
History entries now preserve nullable `error_code` so recovery also works after reopening
or replaying a chat. This does not persist raw availability metadata or widen Google access.

### Calendar read follow-ups (conversation 1.4.1)

The conversation request/response envelope is unchanged. After a Calendar answer,
“check now” or “try again” can repeat the prior read without resupplying its date.
The internal `retry_calendar_read` tool has no arguments: the backend loads only
the authenticated conversation's saved request, checks current Calendar selections
and returns fresh evidence with the existing coverage/error fields. It cannot
authorize an event write. Explicit new dates use a new Calendar read instead.

A retry preserves the requested day and uses the current clock for remaining-day
availability. Unknown coverage remains unknown; retrying does not suppress failed
calendars. An unrelated completed turn closes the active Calendar request. Legacy
conversations lacking a reliable original date may require one clarification.

### Persistent extension login

Google exchange and `/auth/refresh` now return an additional `refresh_token`. This
is a Threadly renewal JWT, not a Google credential. The extension retains the pair
in local extension storage restricted to trusted extension contexts. Renewal uses
`Authorization: Bearer <refresh_token>` only on `/auth/refresh` or `/auth/logout`.
Every other protected endpoint rejects renewal credentials. A valid legacy access
JWT can bootstrap the pair without another Google consent flow.

Access tokens retain `JWT_TTL_MINUTES` (default 24 hours). Renewal is bounded by
`SESSION_REFRESH_DAYS` (default 30, allowed 1–90); renewal never advances the
original deadline, even when called with an access token. New access tokens carry
`token_use: access` and `session_exp`; renewal tokens carry `token_use: refresh`
and the fixed deadline as `exp`. Access expiry is capped at that deadline.
Expired, tampered and revoked credentials return 401. All responses remain
no-store. Logout, disconnect, account replacement and deletion invalidate the
renewal credential through the existing live `av`/`sv` checks and locked refresh.


### Direct Calendar events and chat approval (conversation 1.5.0)

`prepare_calendar_event` is a terminal conversation tool for a user-requested,
one-time event without an email context. It accepts literal user title, location,
description, attendee addresses and calendar name; structured `date` with
`date_source`, normalized `time` with `time_source`, and optional literal
`duration_phrase`. Missing title/date/time prompts retain user details and the
original date anchor for 15 minutes. `continue_previous: true` fills that pending
request. Unrelated completed turns clear it. Unsupported recurrence or end-time
constraints are clarified. Saved Calendar timezone and default duration apply;
explicit times may fall outside working hours. Dates are bounded to the next 90 days.

The result has `kind: calendar_event`, `calendar_action_id` and a fresh
`calendar_action` using the existing Calendar action API. The preview includes the
resolved start/end/timezone, destination name, guests and invitation behavior.
History retains only the action ID; clients reread its status on restore. A queued
or uncertain event is never described as created. Missing event write scope returns
`calendar_write_scope_required` and `calendar_connection_required: true` for setup.

Authenticated UI control, unavailable to the model:

- `GET /assistant/conversations/{uuid}/calendar-approval` →
  `{mode: "ask" | "always", version: integer, scope: "calendar_events_this_chat"}`.
  An unused chat defaults to ask without creating a record.
- `PUT` the same path with `{mode, expected_version}` saves a versioned setting;
  stale versions return 409, another owner's chat returns 404. Chat retention
  limits apply. The mode is bound to the account and current sign-in generation.
- Ask mode creates a proposed exact payload for the existing `/calendar-actions/{id}/approve`
  endpoint. Always mode uses the saved chat permission to create the same exact
  approval record and queue the same worker; the model cannot supply authorization.
  It includes sending invitations to explicitly supplied email addresses.
- Switching to Ask, deleting the chat, signing out, or changing account/preferences
  prevents queued automatic events from dispatching. Events already dispatched
  reconcile by exact provider ID; revocation cannot recall them. New chats ask again.

Calendar writes still require the pilot allowlist, enabled reconciliation and Google
`calendar.events` consent, alongside existing Calendar read/list grants. The worker
rechecks live destination ACL and selected-calendar free/busy immediately before
insertion. No automatic overwrite of busy time or unknown calendar coverage occurs.


### Spoken Calendar requests and interrupted chat recovery (conversation 1.7.2)

Direct event creation also accepts time-first requests such as `book 2 p.m. tomorrow
for doctor's appointment`. Dotted AM/PM has the same meaning as plain AM/PM. Pending
follow-ups retain supplied slots when the model emits empty optional defaults.
Recognized event requests must pass through typed preparation; prose is not evidence
of creation. Status replies read the owned action's current state. Exhausted Calendar
preparation completes the turn with HTTP 200, `kind: message` and
`error_code: calendar_event_not_prepared`, retaining unexpired missing-field context.
No event, approval or job is inferred from that response.

`check_time_availability` is a terminal read tool for `am i free at 2 pm tmrw?`.
It takes the existing date/subject fields, normalized `at_time`, exact
`at_time_source`, and optional literal `duration_phrase`. It rejects paired clock
fields, ambiguous/nonexistent wall times, dropped constraints and multi-day windows.
The checked interval uses and displays the requested duration or saved meeting
duration. Transient `calendar_tools` adds resolved `date`, `start`, `end`, `timezone`
and `duration_minutes`; incomplete coverage never establishes free time. A later
`retry_calendar_read` preserves that civil date/time and reads current selections.

Authenticated `POST /assistant/conversations/{id}/recover` accepts:

```json
{"pending_request_id":"issued-request-uuid","expected_version":0,"operation":"recover"}
```

`operation` is `recover` or `cancel`. Use the owned GET's pending ID/version.
GET's `pending_recovery` is null without a pending request; otherwise it contains
`active` and `has_saved_result` booleans. These are observations, not permission to
cancel. POST rechecks owner, version, pending ID/hash, lease and linked work.

- Recover finalizes a checkpointed result and hydrates the current task/proposal/action.
- If no result was saved, 409 `conversation_result_unavailable` permits the UI to offer
  a separate explicit Cancel action. Cancel succeeds only for a released/expired lease
  with no checkpointed result or linked work. It returns `kind: message` and
  `error_code: conversation_request_cancelled`. It does not cancel a Calendar event.
- Active leases return 409 `conversation_busy`; changed IDs/versions return
  `conversation_pending_changed`/`conversation_version_conflict` and require a reload.
  `conversation_result_available` requires recovery; `conversation_work_exists`
  preserves linked work for review. None of these errors clears the pending request.
- A successful response includes `recovered_request_id`, conversation ID and version.
  Original issued-key retries replay the receipt, including when the original turn
  completed concurrently; no new turn or duplicate action is created. The original
  request hash remains bound. Recovered history has `recovered: true` and `user: ""`;
  display an assistant-only result rather than inventing user instructions.

Recovery is a UI operation, unavailable to model tools. It never approves or dispatches
provider work. Saved tasks/actions retain existing execution and uncertain-outcome
reconciliation. A late worker with the old lease cannot commit its candidate.


### Structured Calendar destination selection (conversation 1.7.2)

Creation retains a title, date, time and destination through missing-field replies,
including `Meeting at4pm` → `tomorrow`, or `Meeting` → `tmrw` → `4 pm`. Only the missing
field is requested. A relative date first supplied on a later turn uses that turn's
clock; previously accepted dates keep their saved anchor. Calendar discovery during
creation retains the request and offers only selected calendars with write ACL.
Ordinary calendar discovery distinguishes editable, read-only and busy-time-only ACLs;
an editable ACL alone is not OAuth scope readiness or permission to create an event.

A creation clarification and owned conversation GET may include:

```json
{"calendar_choices":{"choices":[{"choice_id":"00000000-0000-4000-8000-000000000001","label":"Work","access":"editable"}],"expires_at":"2026-10-06T06:00:00Z"}}
```

`calendar_choices` is null/absent when no choices are active. Choice IDs are opaque,
issued by the backend and bound to one pending creation, chat/account, preferences
and display order. They contain no provider IDs. Names/emails are matched against
current owned eligible calendars; duplicate names require a choice. A literal ordinal
uses the saved displayed order even if Google reorders its list. Merely mentioning or
quoting an ordinal does not select a calendar.

`POST /assistant/conversations/{id}/calendar-choice` accepts
`{request_id: UUID, expected_version: integer, choice_id: UUID}`. Use the version from
the surrounding response/GET; retain the exact body across transport failures.
This authenticated UI operation bypasses the model and resumes the saved event.
It cannot supply event fields, switch approval modes, or authorize a different action.
Ask returns the existing proposed event card; Always uses only the existing saved
grant. A missing field returns another clarification with the chosen calendar retained.

The response has the ordinary conversation ID/version and current event or choices.
Expired/unknown references return a terminal HTTP 200 clarification with
`calendar_choice_unavailable`; stale preferences/ACL/destination return
`calendar_choices_changed` with fresh eligible choices where available. No replacement
is automatically selected. Normal 409 owner/account/version/busy/retry/idempotency
fences apply; a changed body under the same request ID is `idempotency_conflict`.
Concurrent clicks cannot create two candidates. The original key replays its result.
Selection results, including remaining-field clarifications and their choice payload,
are checkpointed for `/recover`; recovering never upgrades Ask into approval.

The current picker UI must consume `calendar_choices` and send the selection endpoint;
labels must not be converted to model instructions. The matching frontend picker
release is an integration dependency for the clickable controls.


Conversation release 1.7.3 retains “could you help me create…” as a direct event
request, including missing-title follow-ups after a provider-unavailable retry.
If calendar choices expire during their ACL read, selection returns terminal
`calendar_choice_unavailable` without creating an action; exact retries replay
that result. Ask/Always and all existing request/account/version fences remain.

### Calendar draft continuation contract (conversation 1.8.0)

`prepare_calendar_event` retains the existing fields and `continue_previous` flag.
Its optional `intent` is `{operation: create|resume|revise|cancel, source: string}`;
source quotes the complete top-level user directive. It is an interpretation, never
an approval or Google grant. New explicit creation supersedes an undispatched old
candidate. Availability/email detours retain the unfinished goal for its original
15-minute lifetime. Account ownership/version checks remain unchanged.

Corrections use `continue_previous: true` and `changes`, an array with distinct
fields. Each entry has `field`, `operation`, `source`, and optional `value`:

- `replace`: a string value for title/location/description/calendar_name/duration,
  a structured date for date, a normalized clock for time, or email list for attendees.
  Date/time source quotes the original date/clock words in the latest user turn.
- `clear`: no value; explicitly clears a field. Clearing required fields asks for them.
- `remove`: only attendees, with explicit existing addresses to remove.

Legacy empty values remain no-ops. Changes cannot derive authority from quoted email,
calendar labels, another account, or model history. A correction supersedes the old
undispatched immutable action and retires its approval/job before replacement. A
claimed worker is fenced by that transition. Dispatched or unknown outcomes cannot
be edited/replaced automatically. Repeating a resume without changes returns the
same action; identical HTTP retries still use the existing exact request receipt.
Changed existing details invalidate old calendar choice handles; filling a missing
field preserves an explicitly chosen destination, rechecked against current ACLs
and preference/account versions. Published response shapes and picker endpoints
are unchanged. No migration, OAuth scope or production setting is required.


### Calendar voice follow-ups (conversation 1.8.2)

“Make an event” uses the same preparation path as “create an event”. A model's
truncated intent source, incomplete unambiguous trailing title or attempted
continuation of a new creation goal is repaired within the existing bounded tool
loop before draft mutation. Exhaustion does not promise an unexecuted retry.
Standalone gratitude and conversation-closing phrases return plain messages and
cannot replay an event card through the preparation tool. Proposed direct events
explain when they use the saved default duration; this explanation is tied to the
immutable candidate's source metadata. Request/approval envelopes and execution
authority are unchanged.

### Single-time Calendar availability presentation (conversation 1.8.4)

A `check_time_availability` response keeps `kind: message` and concise `text`.
Its optional `calendar_tools` payload now additionally contains:

- `availability`: `free`, `busy`, or `unknown`; partial checks may report known busy
  intervals but never confirm free time.
- `scope: selected_calendars`, `complete`, and existing `coverage`.
- `duration_source`: `saved_default` or `requested`, plus existing `duration_minutes`.
- `busy_periods`: at most ten clipped `{start,end}` ISO intervals and `busy_period_count`.

Existing `date`, `start`, `end`, `timezone`, `checked_at`, `expires_at`, and `evidence_id`
remain. The payload is live evidence, not an event preview or permission to create one.
Clients should show it alongside the answer, mark expired evidence as historical, and
never infer event titles or another person's availability. Older responses without these
fields remain valid text responses. These card details are not persisted in history.
# Conversational email drafting clarification (1.8.6)

Standalone composition uses the backend model tool `prepare_email_draft` before
reserving a durable workflow. It accepts exact user-sourced `recipient` and
`purpose`, `continue_previous`, and optional generated `draft` text. Missing fields
return a normal `kind=clarification` conversation response without a task or
preparation acknowledgment. The backend asks only for the recipient, purpose, or
both; no subject or exact mailbox is required just to write text.

The bounded encrypted conversation state retains the goal and current recipient
authority across answers and repeated requests. A new goal resets those fields;
a superseded task pointer resent by an older client cannot reactivate the old
draft. Changing the recipient cannot retain the old To address. Previously bound
Cc/Bcc remain unless that role changes. Mail observations cannot supply these
user-only fields.

A named recipient produces `kind=message` with a generated subject and body.
This text has no actionable envelope, artifact approval, insertion, Gmail-save,
or send authority. Literal user-role-bound addresses enter the existing durable
draft worker with a validated compose route (`email-draft-preflight-1.0.0`), avoiding
redundant intent classification. Existing recipient validation, draft review,
exact-payload approval, disabled-intent controls and Gmail send gates still apply.
Source-based replies/drafts and compound workflows keep their existing contracts.
No database migration or external email operation is introduced.
