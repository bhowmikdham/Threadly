# Durable typed clarification — B08

Implemented on `codex/durable-clarification`, based on merged PR #19 (`7de38d8`).
This is the backend continuation slice. It does not install Calendar operations,
the compound executor, BERT inference or external writes. Frontend form integration
and live model evaluations remain separate gates.

## Behavior

A new assistant request can pause with a saved question. The user supplies typed
fields to the **same task**; the backend keeps the original instruction, request
hash, initial context/draft envelope and generation release immutable. Accepted
answers form a separate append-only input history with effective source/envelope
bindings. The saved route is resolved deterministically; an answer such as
`Australia/Melbourne` is never classified as a new request.

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as API
    participant DB as PostgreSQL
    participant W as Worker
    UI->>API: POST /assistant/requests
    API->>DB: Save original request and pinned releases
    W->>DB: Claim, classify original instruction, save route
    W->>DB: Atomically save stopped task and typed question
    UI->>API: GET task after task.finished event
    API-->>UI: needs_clarification + question ID, fields, version, expiry
    UI->>API: POST /assistant/tasks/{id}/inputs
    API->>DB: Lock task; validate question, ownership, version, expiry
    API->>DB: Validate source and bind recipients/reply target
    API->>DB: Save input, consume question, resolve route and requeue
    API-->>UI: 202 + current task and event cursor
    W->>DB: Claim updated effective inputs and saved route
    W->>W: Continue installed operation, without classifying the answer
    W->>DB: Save artifact or next question/unavailable outcome
```

Questions are available for newly accepted tasks with `continuation_release` set.
Historical tasks remain unchanged: no retroactive questions, rerouting, prompt
changes or cancellation changes. Submit a new request to continue a historical
clarification task. The old `AssistantRequest.continuation` field still returns
501; the implemented continuation endpoint is the typed `/inputs` route below.

## Frontend request and response

When a task reaches `needs_clarification`, fetch its current view. Example fields
for “Write an email” without recipients (IDs below are placeholders):

```json
{
  "task_id": "TASK_ID",
  "instruction": "Write an email",
  "state": "needs_clarification",
  "version": 4,
  "input_version": 0,
  "question": {
    "schema_version": "1.0",
    "question_id": "QUESTION_ID",
    "kind": "missing_inputs",
    "expected_version": 4,
    "input_version": 0,
    "fields": ["recipients"],
    "missing_fields": ["recipient"],
    "prompt": "Provide or select: recipients.",
    "expired": false
  }
}
```

The actual question also includes `expires_at`, original `context_snapshot_id`,
`source_hash` and `route_hash`. Render the prompt and controls; hashes are diagnostic
metadata, not UI copy. Copy the current IDs/version; never hardcode example values.

Send `POST /assistant/tasks/TASK_ID/inputs` with normal authentication:

```json
{
  "schema_version": "1.0",
  "request_id": "answer-unique-client-key",
  "expected_version": 4,
  "question_id": "QUESTION_ID",
  "answer": {"recipients": ["person@example.test"]}
}
```

Returns **202** with the current task, `latest_sequence` and `events_url`. New
acceptance sets `state: queued`, increments `input_version`, consumes the question
and emits `task.input_accepted`. A duplicate accepted answer returns the current
task, which may already be running, finished or cancelled; do not assume a 202
replay means it was queued again. The usual event replay endpoint remains finite;
resume using the returned sequence cursor.

Task views add:

| Field | Meaning |
|---|---|
| `question` | Current open typed question, or null |
| `input_version` | Number of accepted answer rounds, initially 0 |
| `continuation_release` | Version/schema/validation/UI-binding contract pinned at acceptance; null for historical tasks |
| `effective_context_snapshot_id` | Current owned context, including an explicitly supplied replacement |
| `effective_draft_input` | Current backend-bound envelope; use it for continued draft previews |
| `resolved_inputs` | Accumulated typed answers, visible only to the task owner |

`instruction`, `context_snapshot_id`, `draft_input`, `request_hash` and generation
`release` retain their original stored meanings. Final draft revisions store the
effective envelope; review/edit code continues to use the artifact revision's
`draft_envelope`. A review is not send approval.

`GET /assistant/workflows` advertises the continuation schema, new-request scope,
maximum answer rounds and question expiry. Its generation operation list still
contains only summary, reply and compose; it does not advertise Calendar execution.

## Answer fields and validation

Use only the fields requested by `question.fields`. A fresh `context_snapshot_id`
may additionally accompany an answer to refresh its existing source. At least one
requested field must be supplied. Partial answers are supported; remaining fields
produce another question, with a new question ID and task version.

| Answer field | Accepted value / backend behavior |
|---|---|
| `context_snapshot_id` | Owned saved capture; source/thread version must still match. After binding a thread, replacement must be a fresh capture of that same thread. |
| `recipients` | 1–20 distinct literal email addresses for To; preserves initial Cc/Bcc and revalidates distinctness across the envelope. No display-name guessing. |
| `reply_message_id` | An actual message inside the owned selected capture; bind original subject/headers in backend code. Supply context too if not already bound. |
| `timezone` | Valid IANA timezone; saved for the original scheduling goal. |
| `duration_minutes` | Integer 5–480; booleans and numeric strings rejected. |
| `date_phrase` | Nonblank string, maximum 200 characters; preserved for future deterministic time resolution. |
| `time_phrase` | Nonblank string, maximum 100 characters; not a verified available slot. |
| `am_or_pm` | `AM` or `PM`; conflicting time suffix rejected. |

No free-form new instruction, action approval, provider ID selection, Flow ARN or
executable operation is accepted. A bare “yes” cannot authorize mail/calendar work.
Names need explicit recipient resolution; there is no automatic directory lookup.
B08 does not add Calendar IDs to this form or assume a calendar is authorized.

For a selected/ordinal message, capture schema 1.1 with a valid UI map is required.
A plain chronological thread snapshot cannot answer a visual-position question.
The supplied map must resolve the requested message; missing selection, wrong
surface or unusable message text is rejected before consuming the question.
Inbox/thread/option positional resolution remains outside the B07 supported scope.

Worker claims carry `resolved_inputs` and `request_created_at`, alongside the
effective context/envelope and saved route. The original task's `created_at`
remains the request-time anchor. B12/B13 must use
that anchor plus the saved timezone when resolving relative dates, rather than
worker restart time or the last answer's timestamp. B08 stores phrases and context;
it does not claim to have resolved DST ambiguity or checked availability.

### Resolve context before asking

For new tasks, check explicit time context both before opening the first question
and after each typed answer. “4” with “tomorrow afternoon” resolves to 4 PM; the
question omits AM/PM. An explicit saved AM/PM answer remains part of subsequent
rounds. A single selected source message may supply an exact matching clock suffix;
multiple messages require an explicit reply-message selection. The saved route
records where a resolved suffix came from.

This is a bounded deterministic resolver, not general conversational time reasoning.
Conflicting alternatives, negation, quoted history and unrelated clock times leave
the ambiguity unresolved. Office hours alone do not imply PM. The original user
request takes precedence over source text; email content never authorizes actions.
The eventual Calendar resolver must still validate the date, timezone, DST and
actual availability. Broader extraction of context-dependent dates/durations is a
B12/B13 prerequisite, not a capability added by this clarification endpoint.

## Recovery, concurrency and outcomes

- The task lock serializes input, cancellation and worker transitions. Question ID,
  task version, input version and route hash must match; one question can have only
  one accepted input. Same key/different payload returns 409.
- Input history, question consumption, route change, requeue and event are committed
  together. A failure before commit rolls them all back. No provider call happens
  in the answer transaction. Network generation remains in the worker.
- Fresh source validation holds a shared thread lock until the answer commits.
  A changed source requires explicit recapture; stale data is not silently rebound.
  Initial and effective context FKs both cascade task deletion, preserving existing
  privacy/deletion behavior even for tasks initially accepted without context.
- A partial answer starts another bounded worker attempt cycle. Maximum five answer
  rounds per task, at most three worker claims per round (including the initial
  cycle: at most eighteen claims). Question expiry is 24 hours. These bounds are
  versioned defaults, not measured user-behavior thresholds.
- If the fifth answer still leaves missing inputs, stop as `unsupported` with
  `clarification_limit_reached`; submit a complete new request. Unknown question
  fields stop with `clarification_fields_unavailable`, not a fabricated form.
- New clarification tasks can be cancelled with the existing versioned cancel
  endpoint. Expired questions cannot resume. Same accepted request-key replay after
  cancellation never revives the task. Cancellation racing an answer has one winner;
  the loser receives a version/question conflict and must reload.
- Meeting requests remain `plan_schedule` after timezone/duration answers. Once
  their inputs are complete, current code returns `workflow_not_available` because
  Calendar/compound handlers are not installed. This is not a successful schedule.

| Response code | Frontend response |
|---|---|
| 404 `not_found` / `question_not_found` / `context_not_found` | Refuse inaccessible task/question/source; never substitute another account |
| 409 `idempotency_conflict` | New payload needs a new answer key; first reload current question |
| 409 `question_changed` | Reload; discard stale form and do not retry the old answer as new work |
| 409 `question_expired` | Start a new request or cancel |
| 409 `source_changed` | Sync/capture again and submit with current question/version |
| 409 `source_scope_changed` | Select the same thread, or create a separate request for another thread |
| 409 `continuation_unavailable` | Historical task; create a new request |
| 422 `validation_error` / `answer_field_not_requested` | Correct the typed fields |
| 422 `reference_not_resolved` | Capture a supported mapped message/selection; the question is still open |
| 422 `conflicting_time` / `invalid_recipients` | Correct inconsistent or overlapping input |
| 503 `release_unavailable` | Deploy compatible API/worker; do not change the saved release silently |

## Migration, validation and next step

Migration `f2b6049c7a81` follows `e9b7120c4a63`. It adds the pinned continuation
marker, input counter and effective context FK to tasks, plus `task_questions` and
`task_inputs`. Existing tasks get SQL NULL marker and input version 0; no old task
is retrofitted. Composite FKs enforce task/question/context ownership.

Stop old API and workers before migration; apply Alembic, then start matching new
API/workers. Older workers do not create the new questions. Downgrade refuses while
continuation state exists instead of deleting history or reinterpreting tasks.
Drain/cancel alone does not remove history; use a reviewed data-retention/rollback
plan rather than deleting application data to force a downgrade.

`tests/test_continuation.py` provides synthetic deterministic route/answer replays
and actual database/HTTP/worker tests. The migration suite verifies empty install,
old data preservation, new-state downgrade rejection and metadata parity. See
[the B08 checkpoint](backend-execution/checkpoints/B08.md) for final checks.

Next: frontend typed forms and staging verification; B09 grounded read operations;
then B10 compound planning/execution according to its dependencies. Calendar
B12/B13 and external approval/execution remain separate work packages. No AWS
resource, prompt or live mailbox mutation is required for this continuation PR.
