# Reviewed compound-command planner (B10c)

Baseline: merged PR #30 `8cfd020de17b1b5c33d978db8e5e19cda4899279`.
This slice translates a complete command into a **saved plan for review**, then
explicitly confirms it into an existing compound task. It does not silently select
one classifier label, install missing handlers, or enable automatic dispatch.

## Supported scope

The compiler accepts exactly these installed combinations, regardless of label or
mention order:

| Requested work | Compiled template | Data dependency |
|---|---|---|
| Summary + reply | `summary_then_reply` | Summary included only when explicitly requested |
| Summary + compose | `summary_then_compose` | Summary included only when explicitly requested |
| Literal captured-text search + reply | `lookup_then_reply` | Draft depends on the saved lookup |
| Literal captured-text search + compose | `lookup_then_compose` | Draft depends on the saved lookup |

Both steps run in the existing durable worker. Plan confirmation never sends mail,
books a meeting or approves an external action. The existing single-intent
`POST /assistant/requests` and explicit `POST /assistant/compound-requests` remain
unchanged. Single-intent plans submitted here are unsupported in this initial slice;
clients must not silently move a rejected multi-intent command to a single-intent API.

Schedule/Calendar, mailbox-wide or semantic lookup, non-calendar planning, multiple
independent drafts, three-step graphs and requested send/book steps are unsupported.
If ANY requested operation makes the whole combination unavailable, no generation
task or partial step is created. A negated operation is retained as a prohibition,
not counted as a requested step. A requested/prohibited contradiction needs clarification.

## How classification becomes a workflow

```mermaid
sequenceDiagram
    actor User
    participant API as Backend API
    participant DB as PostgreSQL
    participant AI as Model client / Bedrock
    participant Worker as Existing durable worker
    User->>API: Full command + owned source/recipient selections
    API->>DB: Reserve idempotent command plan and pinned releases
    API->>DB: Commit and release source locks
    API->>AI: Numbered command words only
    AI-->>API: Clause spans, operations, prohibitions and dependencies
    API->>API: Validate all spans and compile whole supported combination
    API->>DB: Save immutable interpretation and exact review hash
    API-->>User: Plan to review, missing inputs, unsupported outcome or failure
    User->>API: Confirm exact plan hash and complete-command review
    API->>DB: Lock plan; recheck source, release and full compilation
    API->>DB: Atomically accept one compound task and consume plan
    API-->>User: 202 + existing task/events/step contract
    Worker->>DB: Claim and run pinned compound steps
```

The planner is one bounded model-client call, using the configured Bedrock model in
Bedrock mode. No new AWS master Flow or classifier integration is required. Generation
continues through the saved operation registry and its existing Bedrock Flows.
BERT labels, source email, recipients, account IDs and Flow identifiers are not
planner inputs. Only the user's command is sent through the established cloud-masking
adapter. Typed source/recipient selections remain backend-owned.

Commands are numbered by whitespace-delimited words. The model returns inclusive
word spans; the backend reconstructs original text itself. This allows PII masking
without requiring the model to echo original email addresses or names. Spans must
cover every word exactly once, in order, without gaps/overlaps. Each clause is
`requested`, `prohibited` or `context`, with allowed operations only. A literal search
query must refer to words within a requested capture-search clause. Matching outer
ASCII quotes are removed; arbitrary paraphrased/model-invented queries are rejected.

**Structural coverage is not proof of semantic completeness.** A model could mislabel
an action as context or miss negation. Therefore the first release requires explicit
review of the original command, every clause, requested/prohibited operations,
summary dependency, source scope, recipients and exact compiled draft instruction.
Offline reference replays do not justify automatic dispatch. Live command-domain
holdout evaluation and agreed acceptance thresholds are still required.

## API: propose, inspect, confirm

All endpoints use the current authenticated user and standard `ApiError` envelope.

`POST /assistant/command-plans` → 202:

```json
{
  "schema_version": "1.0",
  "request_id": "unique-command-request",
  "instruction": "Summarise this thread. Draft an email including that summary. Do not send it.",
  "context_snapshot_id": "owned-capture-uuid",
  "draft_options": {
    "to": ["selected-recipient@example.test"]
  }
}
```

For a reply, select a captured `reply_message_id` in `draft_options`. The original
command is limited to 4,000 characters. Context and draft options are optional at
proposal time so knowable missing inputs can be reported together. Supplied IDs
must belong to the caller; existing source/reply validators still apply.

Response fields: `plan_id`, `state`, original `request`, `result`, `plan_hash`,
`release`, `expires_at`, nullable `task_id`, `external_actions: false` and
`requires_complete_command_review: true`. `result` contains reconstructed clauses,
requested/prohibited operations, summary usage, missing fields, clarification
questions, exact compiled request (only for `proposed`), and model provenance.
Failed interpretation stores a sanitized reason, not raw invalid model output.

`GET /assistant/command-plans/{plan_id}` returns the same owned record. Unknown and
foreign plan IDs return 404. Do not treat model questions or clause text as executable
UI instructions. Render text and the backend-defined fields safely.

`POST /assistant/command-plans/{plan_id}/confirm` → 202 existing task response:

```json
{
  "plan_hash": "exact-64-character-hash-returned-by-the-plan",
  "confirm_complete_command": true
}
```

The example hash is a placeholder; copy the actual returned hash. Confirmation
accepts no modified recipients, clause list, query, template or Flow ID. Any change
requires a fresh proposal with a new request ID and a fresh review. An ordinary
chat “yes” does not call this endpoint. Repeated/concurrent confirmations return
one task ID; the consumed plan remains linked to it even if that task later fails.
Task acceptance and plan consumption commit in one transaction. Caller rollback
creates neither an orphan task nor a consumed plan.

## State, clarification and failure behavior

| State | Meaning / next action |
|---|---|
| `planning` | Reservation committed; the first request is performing one bounded interpretation. A duplicate request reads this state and does not start another call |
| `proposed` | Whole combination is installed and inputs are present; show exact review, not a success result |
| `needs_clarification` | Missing bindings, ambiguous dependency/reference, or contradictory operations; correct typed selections and/or command in a **new proposal**, preserving the old record |
| `unsupported` | Whole operation set is not installed; zero execution, no supported-subset fallback |
| `failed` | Invalid/oversized/model output or provider failure; sanitized reason, no generation task; a new request is needed |
| `expired` | Unconsumed planning/proposed record is older than 15 minutes; new proposal required; a late model response cannot revive it |
| `consumed` | Explicit confirmation created/reused exactly one durable task; follow that task's events/results |

Known missing fields: `source_context`, `recipients`, `reply_target`,
`remove_reply_target`, `summary_usage`, `literal_query`. Clarify only unresolved
inputs. Already selected source/recipients are not requested again. An unclear
summary dependency does not default to including it. This is not yet an in-place
B08 plan editor or arbitrary short-answer interpreter; clarification currently
creates a new immutable proposal with the corrected full input.

One interpretation call, 45-second timeout, 3,000 output tokens, 24,000 response
characters and at most 20 clauses/five short ambiguity questions. No repair loop,
automatic inference retry, recursive planner or tool call. If an API process crashes
after reservation, the row remains visible and expires; repeated use of its request
ID does not spend again. Generation retries remain owned by the existing worker.

Same request ID + same normalized typed input replays the plan; changed input gives
`idempotency_conflict`. Confirmation rechecks the immutable hash, recompiles the
saved proposal, validates source ownership/version/digest and requires the exact
pinned generation release. Stale source, altered plan or changed configuration stops
before task creation. Historical requests/releases are not reinterpreted.

## Storage, migration and rollout

New migration: `b10c026e9a31`, parent `a0426e9bc731`. `command_plans` stores immutable
request/hash, source binding/digest, releases, state, result/hash, expiry and optional
owned task reference. Composite foreign keys reject foreign contexts/tasks; a DB
trigger prevents changing original inputs, rewriting published results, reopening
terminal plans or replacing a consumed task. Source/task deletion cascades to its
plan record under the existing owner lifecycle. Downgrade refuses while plan rows
exist; retain user history instead of deleting it for rollback.

Deploy matching migration/API/worker as the existing runbook prescribes. No new
model, Google scope, AWS resource or write flag. Existing worker kernels execute the
confirmed task; old task formats and compound release hashes are unchanged. No
frontend implementation or live deployment is included in this PR.

## Evaluation and next gates

`backend/tests/fixtures/command_plans_v1.json` holds 14 synthetic command/interpretation
references and a pinned contract hash. Tests cover included/separate summaries,
reverse mention order, both lookup pairs, a scheduling triple, positive send,
negated reply, contradictory clauses, duplicate requested outputs, mailbox search,
missing query, and ambiguous reference. Malformed spans/JSON/keys are rejected.
Real PostgreSQL tests cover owner isolation, zero-job blocked plans, concurrent
reservation/confirmation, replay, source/configuration/hash changes, rollback and
expiry. Migration tests exercise actual triggers and preservation/drift checks.

These fixtures verify deterministic compilation and rejection, not live model
accuracy. Next gates: live planner quality, corrected-plan continuation/versioning,
broader retrieval/graphs, B11 planning and B12/B13 Calendar foundations. Calendar
triples stay blocked until all required handlers and their gates are installed.
