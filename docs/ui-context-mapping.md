# Saved UI message references

B07 backend slice, branch `codex/ui-context-mapping`, based on merged PR #14
(`3b51647`). This is implemented code with synthetic integration evidence; the
extension adapter and live model quality evaluation are still pending.

A message's place on screen can differ from its place in the backend's chronological
list. The extension supplies an ordered list of IDs. The backend checks that every
ID belongs to the signed-in user's synced thread, loads its own cleaned text and
saves the map and excerpts together. It never trusts a browser-supplied email body.

```mermaid
sequenceDiagram
    participant UI as Extension
    participant API as FastAPI
    participant DB as PostgreSQL
    participant W as Durable worker
    participant AI as Native model or Bedrock Flow
    UI->>API: Capture schema 1.1, thread version, visible IDs, selection
    API->>DB: One owner-scoped source/version query
    DB-->>API: Authoritative synced excerpts
    API->>DB: Save immutable context and map hash
    API-->>UI: Snapshot ID and captured map
    UI->>API: Request + pinned snapshot ID + unique request ID
    API->>DB: Atomically save task, pinned release, job, event
    W->>DB: Claim task and load owned snapshot
    W->>W: Bind ordinal or selection to one saved message
    alt Exact excerpt request
        W->>W: Build source-linked answer from saved text
    else Single-message summary
        W->>AI: Only the bound excerpt and fixed summary request
        AI-->>W: Strict summary JSON
        W->>W: Validate citations against that one message
    else Missing or ambiguous map
        W->>DB: Save needs_clarification, no inference
    end
    W->>DB: Publish only while task lease remains valid
    UI->>API: Read task, events and artifact
```

## Frontend integration recipe

1. Read the owned `GET /threads/{thread_id}`. Use `thread.version` and message
   `gmail_msg_id` values. Version starts at **0**, not 1. Arrange messages in the
   actual display order; do not send DOM element IDs or assume API order is UI order.
2. When capturing a thread view, submit the following body to
   `POST /assistant/context-snapshots`. The timestamp is an example: generate it
   at capture time. Include only messages whose IDs are known and mapped by the UI.
3. Keep the returned snapshot ID with its context chip/conversation. On navigation
   or a changed selection, capture a new snapshot. Do not silently replace the chip
   on a follow-up that refers to the previously captured view.
4. Submit the usual schema `1.0` assistant request with that snapshot ID. This is a
   new request with `continuation: null`; durable clarification answers belong to B08.
5. Render `answer.content.text` and `evidence[].quote` as **untrusted plain text**.
   They can contain source instructions or markup; never execute them. Show the
   source link and saved-context/coverage notices. Copy/Insert are separate actions.

```json
{
  "schema_version": "1.1",
  "thread_id": "thread-one",
  "ui_map": {
    "schema_version": "1.0",
    "surface": "gmail_thread",
    "thread_version": 0,
    "captured_at": "2026-09-15T16:00:00+10:00",
    "visible_message_ids": ["m3", "m1", "m2"],
    "selected_message_ids": ["m1"]
  }
}
```

With this view, `What's in the third message?` returns **m2**. `Show the selected
message` returns **m1**. The `messages` array in the response stays chronological;
only `ui_map.visible_message_ids` defines screen positions. The backend treats UI
order as the client's assertion, not independently verified screen contents.

```json
{
  "schema_version": "1.0",
  "request_id": "unique-followup-key",
  "instruction": "What's in the third message?",
  "intent_hint": null,
  "context_snapshot_id": "<saved snapshot ID>",
  "continuation": null
}
```

The existing task/events/artifact APIs return this result. `route.reference_binding`
contains the mode, message ID, source version and map hash. For a native extraction,
`decision.operations` is empty, `intent` is `other`, `output_kind` is `answer` and
`requested_action` is `none`: this is a backend rule, not a model-selected tool.
Provenance uses `provider: native`, `model: null`. Answer content has `text`,
`claims[{text,evidence_ref_ids}]`, and `search_scope: saved_ui_message`. Its evidence
contains exactly the saved message's ID/hash and cleaned excerpt. A summary retains
the existing summary schema with only the bound message in its evidence list.

`GET /assistant/workflows` advertises `ui_context.capture_schema`, supported
surfaces and reference handlers. This describes installed code, not a completed
browser/AWS integration test.

## Supported language and boundaries

| Request | Behavior |
|---|---|
| `What's in the third message?` / `What does the 3rd email say?` | Exact saved excerpt, no inference |
| `Show [me] the selected message` / `Read this message` | Exactly one captured selected ID required |
| `Summarise the third message` / `Summarize this message` | One-message summary through the pinned native or Flow implementation |
| `Summarise this thread` | Existing summary of captured excerpts, still partial coverage |
| `What's in the third thread?` / `Show the third option` | Clarification; a message map cannot identify a thread or slot |
| `Show the 50th message` when only three are captured | Clarification; no fallback to the last message |
| Two referenced messages in one request | Clarification; multi-reference execution is not installed |
| `Summarise the third message and send a reply` | Unsupported; no partial summary or send |
| `Why did the third message mention that?` | Unsupported in this bounded handler; broader grounded Q&A is B09 |

Exact supported forms allow optional leading `please`, optional `the`, case and
whitespace differences, and trailing `?`, `.` or `!`. English ordinals first through
tenth and numeric forms such as `11th` are supported. `this message` and `selected
message` require a single selection. A bare `this` retains the existing router's
behavior; frontend selection actions should send the explicit `selected message`
form. Free-form styles/compound reference actions, inbox thread maps, schedule
option maps, arbitrary selected text and screenshot/OCR capture are not installed.
The UI should send supported actions directly instead of relying on a model to
convert unsupported phrasing. No model is asked to invent a missing mapping.

## Validation and storage

- Capture schema `1.1` requires one `gmail_thread` map, 1–50 distinct visible IDs,
  0–50 distinct selected IDs, and selection as a subset of the visible list.
- Client timestamp must contain a UTC offset and be within 15 minutes in the past
  or 5 minutes in the future at capture. It is client metadata; the separate response
  `captured_at` is the server's save time. Accepted snapshots do not expire on this
  timer: future reads explicitly use the saved view, not a live screen.
- A single SQL statement checks owner, thread membership, IDs and thread version.
  Missing, deleted or inaccessible IDs return **404 `ui_reference_not_found`**.
  A changed thread returns **409 `ui_context_changed`**; expired/skewed captures
  return **409 `ui_capture_expired`**. Recapture instead of guessing replacements.
- Extra keys, duplicate/oversized IDs, other surfaces and invalid selections return
  **422** using the existing error envelope. There is no client body or selected-text
  field; importing arbitrary text requires a separate user-input contract.
- Text comes from synced, cleaned records. A total **12,000-character** body budget
  is divided equally across visible messages, keeping every visible ID available
  even when text must be truncated. Unused per-message space is not redistributed.
  Empty targets request `message_text` clarification; an entirely empty view is
  **409 `context_empty`**. Attachments and full-mailbox coverage are not claimed.
- Context payload schema `1.1` stores `scope: synced_ui_message_excerpts`, the map,
  truncated message IDs and capture policy alongside existing counters. It uses
  the existing immutable JSONB snapshot table, with no new migration.
- Later sync edits/deletion of an individual message do not rewrite historical
  snapshots. Existing account/thread deletion cascades remove contexts/tasks;
  publication still checks ownership, task state and lease fencing.

## Releases, testing and rollout

Requests using schema `1.1` snapshots pin `ui-context-task-1.0.0`, wrapping the entire
existing native/Flow release. The wrapper hashes reference grammar, map schema,
capture policy and the fixed one-message summary request. Base routing/summary
prompts and Flow graph contracts are unchanged. Historical schema `1.0` snapshots
and queued summary/contextual/Flow releases retain their original behavior.
Changing a release contract fails closed; changing the active Flow registry does
not retarget a queued task. Source filtering happens before the shared generation
adapter, including its existing cloud masking and strict artifact validation.

Deploy compatible API and worker code together before clients enable capture 1.1.
An older worker cannot execute these new releases. To roll back, stop new 1.1
captures and drain/cancel their tasks with the compatible worker before reverting.
Old saved snapshots/artifacts require no backfill; no AWS resource or OAuth change
is needed to use native extraction. Configured Flow generation still requires the
published runtime contract described in [workflow runtime](workflow-runtime.md).

Replay evidence is in `backend/tests/test_ui_context.py`; use Python 3.12+, backend
dev dependencies and a **disposable** PostgreSQL 16 database. Never point tests at
an existing mailbox database; the suite drops/truncates its configured tables.

```bash
THREADLY_REQUIRE_TEST_DB=1 \
THREADLY_TEST_DB=postgresql+asyncpg://threadly:change-me@127.0.0.1:55444/threadly_test \
PYTHONPATH=backend python -m pytest backend/tests/test_ui_context.py -q
```

The tests compare screen and chronological order, owner/scope rejection, unchanged
saved evidence after navigation/sync, exact native quotes, scoped native/Flow summary
validation, ambiguity, malformed captures, tampered checkpoints, cancellation,
request replay and old releases. They use synthetic mail/model/SDK responses.
They do not establish Haiku language quality or actual extension integration.

Next dependency: B08 durable typed clarification questions/answers. B09 can reuse
this binding for bounded grounded Q&A; B10 owns compound operations. Frontend work
must connect real visible IDs and test multiple context chips, navigation, collapsed
messages and honest partial coverage before calling the screenshot scenario live.
