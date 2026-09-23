# Architecture — what runs where

> Current inbox chat: [conversational search and result cards](inbox-chat.md).

> Current selected-thread behavior: [grounded answers and receipt fixes](grounded-thread-fixes.md).

> Current correction: [on-demand Gmail](on-demand-gmail.md) supersedes the mailbox-sync,
> local-search and stored-source assumptions below. Default runtime fetches selected sources
> from Gmail and stores references only; bulk sync is retired. See that contract before integration.

Source of truth for the system topology. Mirrors the "MailMind — Dockerised
Backend Topology" diagram from the project docs (the PDF lives in the team's
Claude project / drive). Every box below maps to exactly one folder in this repo.

## Topology

Implementation update (13 September 2026): [ADR 003](decisions/003-bedrock-migration.md)
supersedes the inference placement below. `INFERENCE_PROVIDER=bedrock` routes
generation through the new Bedrock Converse adapter. `legacy` retains the
original topology during migration. Durable contextual requests now use a
separate native backend worker and PostgreSQL state; Bedrock Flow invocation
remains planned in the [implementation playbook](implementation-playbook/README.md).

```
Chrome extension (frontend/) ──HTTPS 443, REST + SSE──▶ AWS EC2 (t3.small+, Elastic IP)
                                                        security group: 443 + 22 only
                                                        billing alarm: $20

  docker network: threadly_net (compose-managed)
  ┌────────────────────────────────────────────────────────────┐
  │  caddy :443 ──reverse_proxy──▶ api :8000 (FastAPI)         │
  │  auto-TLS, SSE passthrough      │        │                 │
  │                              SQL│        │vectors          │
  │                        postgres:16     chroma              │
  │                        13 tables       per-user            │
  │                                        sent-mail embeddings│
  │  named volumes (survive redeploys): pgdata · chromadata ·  │
  │  caddy_data (certs)                                        │
  └────────────────────────────────────────────────────────────┘
          │ Tailscale (private) :11434         │ HTTPS
          ▼ PII-MASKED payloads                ▼
  Mac M4 16GB — Ollama                  OpenRouter (fallback when Mac down,
  qwen3.5:4b + LoRA (PRIMARY)           long-thread summaries, ablation Tier-1)
  qwen3.5:2b (voice/planner)
  both resident, no swap                ElevenLabs (STT/TTS, keys server-side only)
                                        Google (OAuth test mode 100u, Gmail read/send)
```

Rules encoded by this topology:

- Legacy inference runs on the Mac/OpenRouter; Bedrock mode uses AWS managed
  inference (decisions/003). The EC2 box orchestrates in both modes.
- Anything leaving the box for a cloud model or SaaS goes through PII masking first.
- Data stores are off-the-shelf containers; we own the schema, not the images.
- Voice/API keys live server-side only. The extension holds a session JWT, nothing else.

## Inside the api container — module map

Assistant migration: `app/planner/intent_router.py` provides the five-intent
proposal router through authenticated `POST /assistant/route-preview`.
`app/schemas/assistant.py` contains strict runtime contracts and
`app/planner/intent_prompt.py` the versioned prompt. The historical planner below
is retained for compatibility; the preview does not call its broad regex rules.
No additional service/container is needed for routing. Contextual summary execution
uses `app/assistant/` and the optional Compose `assistant-worker` service. Request
acceptance persists context/task/job/event state; the worker claims and commits,
generates outside a transaction, then publishes a fenced artifact. Browser state
and SSE connections do not own execution. Bedrock Flow invocation, planning/scheduling and bounded other
execution workflows remain upcoming work; draft execution is described below. See the
[worker runbook](assistant-worker.md) and [runtime API contract](api-contract.md).

| # | Box (architecture doc)      | Folder                      | Responsibilities                                            | Week |
|---|-----------------------------|-----------------------------|-------------------------------------------------------------|------|
| 1 | API layer                   | `backend/app/api/`          | routes, JWT check, error envelope (R18)                     | W1+  |
| 2 | Auth service                | `backend/app/auth/`         | OAuth exchange/refresh, Fernet-encrypted tokens             | W1   |
| 3 | Sync worker                 | `backend/app/sync/`         | paginate ALL Gmail pages, clean, upsert postgres            | W1   |
| 4 | Planner                     | `backend/app/planner/`      | rules first, 2b JSON fallback                               | W3   |
| 5 | Orchestrator                | `backend/app/orchestrator/` | dispatch by intent (FETCH_ENTITY / SUMMARISE / DRAFT)       | W2+  |
| 6 | Extractor                   | `backend/app/extractor/`    | tier1 regex (90%+), tier2 LLM residue, dedupe               | W2   |
| 7 | RAG service                 | `backend/app/rag/`          | embed sent mail, retrieve(k, cap=3000 chars)                | W3   |
| 8 | Model client                | `backend/app/model_client/` | mac | openrouter, 2s health probe fallback, thinking OFF     | W1   |
| 9 | Voice proxy + PII           | `backend/app/voice/` + `backend/app/pii/` | STT/TTS passthrough; mask on ALL cloud egress | W3   |
|   | Data layer                  | `backend/app/db/`           | engine, 7 table models, repositories                        | W1   |
|   | Request/response contracts  | `backend/app/schemas/`      | pydantic models mirrored by docs/api-contract.md            | W1+  |

Orchestrator principle (decisions/002): **structured data goes AROUND the LLM,
never through it.** Entity lookups return DB rows directly; models only see the
residue that regex/rules can't handle.

## AI team hand-off (`ml/`)

The AI team ships FILES, not services — backend code loads them:

- `ml/classifier/bert.safetensors` + `labels.json` — reply-or-not / priority classifier
- `ml/adapter/` — QLoRA adapter (applied to qwen3.5:4b on the Mac, via Ollama Modelfile)
- `ml/prompts/prompts.yaml` — versioned prompt templates

Mounted read-only into the api container at `/ml`. Contract details: `ml/README.md`.

## Build order (backend, 4-week plan)

- **W1**: auth (2) → sync (3) → data layer → model client (8) → `/summary` SSE
- **W2**: summary cache · `/threads` · extractor (6) · `/entities`
- **W3**: RAG (7) · `/draft` · voice + PII (9) · planner (4)
- **W4**: load drill · PII hardening · golden tests · freeze

## Gmail source consistency (T02)

`app/sync/worker.py` captures a pre-scan cursor and replays Gmail history before
committing a backfill. All network reads happen outside DB transactions. A
per-user `sync_version` compare under a row lock rejects stale concurrent work;
message changes, deterministic thread heads/versions, cache invalidation and
cursor advancement commit together. Incremental sync includes deletions and
label changes, maintaining the same non-SPAM/non-TRASH scope as backfill.

`app/db/repositories.py:message_order` is shared by thread details, context
capture and legacy summary rendering. Selected RFC reply headers and parsed
mailboxes are stored for later recipient services. The legacy summary cache
checks the captured thread version before publication. Durable summaries retain
their immutable input even after later sync. This remains inline sync, with
large-mailbox/background scheduling work outstanding; see the
[Gmail lifecycle, diagram and rollout guide](gmail-sync.md).

## Durable routing and dispatch (T06 partial)

`app/assistant/routing.py` binds the stateless classifier to authorized snapshot
capabilities. Request acceptance persists instructions before inference; the
existing worker routes, checkpoints under its lease, then runs the installed
summary workflow. Clarification and unsupported outcomes persist as distinct
stopped task states. Uninstalled compound workflows do not execute a supported
subset. Source bodies never enter classification, and only the backend binds
snapshot IDs. New task release manifests pin both routing and generation assets.
See [contextual routing diagrams and handoff](assistant-routing.md).

## Initial reply/compose artifacts (T10/T11/T13 partial)

`app/assistant/drafting.py` binds explicit recipients and a selected reply message
at acceptance, constructs generation input without adding envelope addresses,
validates model text/source numbers and produces a reviewable draft artifact.
`app/assistant/routing.py` now dispatches single summary/reply/compose operations;
compound scheduling requests remain unavailable. `routing_v1.py` retains the prior
contextual release for already-queued work. All generation/publication uses the
existing lease/cancellation protocol. Drafts are stored in Threadly only; no Gmail
writes occur during draft generation. The disabled B05 action worker is described
below. See [draft lifecycle](assistant-drafts.md).

## Draft editing and review (T10 partial)

`app/assistant/draft_review.py` provides a DB-only editing/review service through
the existing assistant API. It serializes mutations on the task row, appends new
artifact/envelope revisions and records exact-hash review acknowledgements.
It does not enqueue a job, call a model or perform a provider write. Saved task
inputs and historical artifacts stay immutable. UI task reads resolve the latest
revision; review validity also checks local reply/source and sender changes.
A separate action proposal/approval/execution service is still required to send.
See [editor lifecycle](assistant-draft-review.md) and [backend remaining work](backend-remaining-work.md).

## Bedrock Flow prototype provisioning

[CloudShell Flow launcher](../infra/bedrock/README.md) provisions six isolated,
proposal-only visual Flow drafts for Haiku 4.5 experiments. It does not change the
runtime topology or enable Flow invocation in the assistant worker. The B16 registry,
B17 read bridge/invocation adapter, backend validators and live evaluation gates
remain required before integration. Prepared AWS resources are not evidence of
working end-to-end Gmail/Calendar workflows.

## Application Flow runtime

The [workflow runtime](workflow-runtime.md) adds a registry and bounded InvokeFlow
adapter for the existing summary/reply/compose worker paths. The backend supplies
masked, owner-bound context before invocation and validates the result afterward.
This restricted release has no Lambda/read callback or write nodes. It preserves
native admission by default, pins configured targets into new tasks, validates
published graph/alias identity and uses the existing cancellation/lease fencing.
The six earlier prototypes use a different output envelope; a compatible, evaluated
runtime release must be published before activation. Other/planning/Calendar, compound
steps, durable clarification and approved external actions remain pending.

## UI reference binding

Capture schema 1.1 adds an owner-checked, immutable Gmail thread-view map.
`assistant/ui_context.py` hydrates selected IDs from PostgreSQL in one consistent
source/version query. `assistant/ui_routing.py` binds supported references against
visible order and returns exact source quotes natively, or filters a single-message
summary before the existing generation adapter. No source text can select another
operation. The worker keeps release compatibility and fenced artifact publication.
Legacy captures retain their semantics; UI-aware tasks wrap the pinned native/Flow
release. [Capture lifecycle and frontend integration](ui-context-mapping.md) covers
bounds, errors, rollback and the remaining B08/B09/browser gates.

## Summary quality release

`summary_policy.py` supplies the versioned concise policy shared by the runtime and
a separate console experiment. `summary_quality.py` builds numbered-source prompts
and applies bounded output checks before the existing artifact/evidence builder.
New tasks pin this wrapper without reinterpreting old queued releases; UI selection
filtering still occurs before generation. Graph completion and schema validity do
not establish factual quality. [The diagnosis and evaluation guide](summary-quality.md)
separate offline regression, live Haiku evaluation and frontend presentation.

The [master workflow contract](master-workflow.md) maps the actual API/worker/router/
registry coordinator and the planned multi-label → validated step graph integration.
Summary policy 1.0.2 separates descriptive output from requested planning; its
offline/live evaluation distinguishes JSON validity from known semantic regressions.
No AWS master orchestrator or compound scheduling executor is added by that fix.


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
read tasks now pin `bounded-reads-task-1.1.0` for capability-aware help.
See [compose routing correction](compose-routing-fix.md) for strict model-output
parsing, prompt versions and live replay evidence.
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


### Explicit compound generation (B10a)

`POST /assistant/compound-requests` saves a fixed, user-selected summary → draft
plan through the existing task service. `assistant/steps.py` executes the pinned
template under the existing worker lease, checks local sources before each stage
and publication, and persists immutable per-step output/checkpoint hashes. The
summary and draft use separate artifact streams; only the final draft commits task
success. Failures retry the missing step while reusing valid completed output.
No transaction spans model/Flow inference. This branch does not add Calendar or
external-write handlers, or an automatic natural-language compound planner.
See [whole workflow/testing map](workflow-testing-map.md) and
[compound contract](assistant-compound-workflows.md).

## Action persistence (B02)

`app/actions/service.py` stores immutable candidates behind owner and task locks,
separately from generation jobs. Draft edits supersede pending proposals in the
same transaction; executing/unknown records remain unchanged. PostgreSQL guards
ownership, legal transitions and preserved unresolved attempts. This is the storage
foundation; B03 below adds proposal/read HTTP routes. No action worker or Google writes are enabled.
See [action lifecycle and rollout](assistant-action-storage.md) for the full mapping
and the source-deletion policy that must be completed before live writes.

## Google auth and capability boundary (B01)

`auth/flow.py` owns one-use state/PKCE and account-bound reconnect.
`auth/google.py` handles sanitized fixed-endpoint HTTP calls; `auth/service.py`
separates credential refresh transactions from caller work and fences late refresh
results. `capabilities/service.py` combines actual grants, verified identity and
credential availability with installed handlers. No send/Calendar executor is
enabled. The [Google lifecycle](google-capabilities.md) records the existing frontend
getAuthToken mismatch, typed API contract and live consent gate.

A separate stdlib laptop test client (`backend/tools/google_local_login.py`) can
complete the same handshake through an SSM tunnel. An off-by-default setting permits
one exact loopback HTTP callback; it adds no endpoint and does not change token
storage, scope authority or write approval. [Setup](google-local-testing.md).

## Exact email proposals (B03)

`actions/email_preview.py` validates the current edited artifact, effective context
and verified Google account before `actions/email_payload.py` builds immutable
plain-text MIME. Public proposal/read routes expose the saved envelope and hashes
with current blockers. B02 supplies transaction ownership, task-first locking,
immutable storage and edit supersession. No model or provider call occurs here;
source/account revalidation remains mandatory at future approval and dispatch.
[Preview lifecycle and B04 handoff](email-action-previews.md).

## Approval and cancellation (B04)

`actions/approval.py` binds request identity to the saved action hash/version, locks
task → action → account before source/grant revalidation, then atomically stores
approval, approved state, dispatch job and event. The internal enabled seam is
exercised with fake dispatch only; public approval cannot enable it. Stop decisions
use immutable receipts and the same task/action lock order. Post-cutoff cancellation
records intent while preserving the running/unknown action and recovery job. No
transaction spans a provider call. [Full lifecycle](action-approval.md).


## Isolated Gmail action worker (B05)

`actions/worker.py` owns leased action jobs, preflight, committed dispatch intent,
result fencing and expiry recovery. `actions/gmail_sender.py` performs one bounded
POST with exact saved MIME. Credentials and provider HTTP run outside task/action
transactions. Crashes after intent become held reconciliation work, never a second
send. Root Compose offers an optional `actions` profile. B05 staging did not start
this worker; B06 below adds deployment wiring and bounded recovery. Environment and
compiled live-verification gates keep writes disabled; public approval remains unavailable.
[Lifecycle, failure policy and rollout](email-actions.md).


## Read-only send reconciliation (B06)

`actions/reconciliation.py` owns bounded read leases/observations and fenced result
publication. `actions/gmail_reconciliation.py` scopes GETs to the original mailbox
and compares one complete Sent candidate against saved plain-text MIME semantics.
New sends from the same task are blocked while an earlier result is uncertain.
`EMAIL_RECONCILIATION_ENABLED` independently gates provider reads; live sends/public
approval remain disabled. Staging now wires the same-image action worker into
stop/migrate/restart, but this branch has not been deployed. [Contract](email-actions.md).

### Captured-text lookup plus draft (B10b)

The explicit compound endpoint now accepts lookup → reply/compose as a separate
pinned contract. `assistant/lookup_draft.py` defines native source selection and
its prompt policy; the existing `steps.py` worker owns checkpointing, leases,
publication and retries for both summary and lookup pairs. Native lookup performs
no external call. Only matched captured messages plus an explicit reply target
enter draft generation; no/too-many matches stop first. The saved runtime registry
still controls the generation Flow. No master AWS Flow, free-text full-command
planner, mailbox-wide automatic retrieval or Calendar executor is introduced.
See [runtime](lookup-draft-workflows.md) and [whole-backend progress](backend-progress.md).

### Reviewed compound-command planning (B10c)

`planner/command.py` interprets numbered user-command words into a strict span/operation
proposal and deterministically compiles one of four installed compound pairs.
`assistant/command_plans.py` persists a reservation before bounded model inference,
then saves the reviewable interpretation. No source email or typed provider IDs enter
the planner. Explicit whole-command confirmation rechecks ownership/source/release
and atomically links one existing compound task. No separate step executor or AWS
master Flow is introduced. Structural coverage cannot prove semantic correctness;
automatic dispatch remains disabled pending live quality evidence. Unsupported
operations block the whole proposal. [Runtime contract](command-planner.md).

### Calendar read foundations (B12)

`calendar/service.py` coordinates JWT-owned account/preferences around bounded
`calendar/client.py` httpx reads. API calls are direct reads, not assistant generation
jobs or model-selected tools. It reuses B01 token refresh, requests narrow Calendar
read scopes only through authenticated reconnect, and requires actual list + freebusy
grants. Transactions close before network calls; short final account/preferences
locks fence publication. Saved evidence has explicit per-calendar unknown coverage,
version checks and expiry. This service makes no calendar write; B14b1 adds the
explicit assistant read handler described below.
[Calendar lifecycle, diagram and B13 handoff](calendar-reads.md).

### Deterministic Calendar slots (B13)

`calendar/slots.py` commits an idempotent owned request/anchor before invoking B12's
read service, then fences publication under fresh account/preferences/receipt locks.
`time_resolution.py` resolves typed relative dates and genuine clock ambiguity;
`availability.py` computes UTC intervals, DST-aware working windows, padded busy
conflicts, notice and up to three options without model arithmetic. Saved explicit
working hours or caller-supplied local context may resolve AM/PM; unknown busy data
cannot do so. Immutable receipts retain assumptions, source versions and stable IDs.

These direct read routes are reused by B14b1's durable typed scheduling handler. B14 owns
negotiation, further assistant integration and fresh slot selection; B15 owns exact approved
booking/recovery. External Calendar changes are not pushed into B12 evidence, so a
five-minute offer always requires fresh validation before booking. Diagram and full
contract: [Calendar slots](calendar-slots.md).

### Meeting negotiation foundation (B14a)

`calendar/negotiations.py` groups immutable B13 offers and explicit selection checks
under an owned synced thread. It locks account → preferences → thread → negotiation,
commits a checking receipt, invokes B13 outside DB transactions, then fences exact-
time publication against the same offer/version/source. Unknown/busy/stale results
cannot select an alternative; superseded checks cannot restore earlier state.

The direct API has no model call, generation task or external write. B14b1 adds typed
assistant scheduling continuation/artifacts. B14b2a adds reviewed command extraction below. Remaining B14b work owns replies and later-
email proposals; B15 owns event approval/execution/reconciliation. Read `usable`/
blockers on historical records. Sync's real update timestamp prevents an old slot
query from passing new-thread adoption after an account-lock wait. Runtime API,
diagrams and recovery: [meeting negotiations](meeting-negotiations.md).

### Typed assistant scheduling reads (B14b1)

`assistant/scheduling.py` runs explicit `check_time`/`suggest_slots` tasks on the
existing durable worker. Acceptance pins owned source, preferences, release and
request/message anchor. A versioned scheduling answer schema reuses B08 storage,
limits and task fencing without altering older continuation releases. B13 resolves
typed constraints; saved working hours/context can eliminate needless AM/PM questions.

Short transactions lock account → preferences → source → task. Google reads occur
outside them; publication verifies a matching owned B13 receipt and current source,
preferences and lease. Stable query keys per task/input allow completed-read reuse
after publication failure. Unknown/elapsed/fewer/no slots have distinct honest
outputs. Artifact GET recomputes usability without rewriting historical content.

This typed handler does not itself call a model; B14b2a preparation is described below.
Generated replies remain open. The explicit handler
does not enable Calendar compound routing or mutate negotiations automatically.
Its query IDs can be adopted through B14a's existing checked offer endpoint.
[API examples, sequence, error and recovery contract](assistant-scheduling.md).

### Reviewed scheduling extraction (B14b2a)

A separate `/assistant/scheduling-proposals` surface reserves a durable receipt,
releases DB locks, calls the configured model once for whole-command clauses and
literal word spans, then deterministically normalizes supported scheduling phrases.
No email source text enters this prompt. The user reviews the saved proposal and
confirms its exact hash before `tasks.submit` receives the pinned original scheduling
binding. Existing B14b1/B13 clarification, Google reads and publication fences remain
in force. Account/preferences/source locks precede proposal/task locks. This is a
backend preparation step, not a newly provisioned AWS master Flow. Workflow/state
charts, unsupported combinations and live-quality gate: [contract](scheduling-extraction.md).


## Combined MVP orchestration (17 September 2026)

See [the complete runtime workflow map](mvp-workflow-map.md) and
[ADR 004](decisions/004-mvp-backend-owned-orchestration.md). The reviewed master
coordinator validates the whole command before selecting installed templates;
backend workers own context reads, deterministic Calendar logic, human waits and
external actions. New `workflow_input` tasks support up to three checkpointed steps.
Non-calendar plans, selected commitments, later-email choices and bounded facts
share existing owner-bound artifacts. Separate Calendar approvals and a stable-ID
executor join the existing Gmail lifecycle. Optional auxiliary generation-only
Flows do not contain Google or Lambda tools. Background sync stages paged reads
before one version-fenced atomic publication. API and all three workers share a
pinned release. Historical partial-feature descriptions above are superseded only
for the bounded paths listed in the linked map; live acceptance remains pending.


### Extension integration reference fix

The Chrome panel uses reference-only schema-1.1 captures. Factual questions with
one resolved email/message reference now enter the grounded-answer generator with
only that message, not the whole visible thread. The deterministic UI binder keeps
its independent source fingerprint and exact ordinal mapping; generation still
uses the existing validated quote contract. It does not authorize writes or split
a compound command. No new tables, mailbox cache, model prompt or AWS Flow required.

Verification: full local PostgreSQL suite **1,146 passed, zero skipped** on
2026-09-23; Ruff passed. Replay cases cover selected/ordinal facts, changed source,
ambiguous selection, mixed requests, and a real task/worker/artifact lifecycle with
a fake model. Live EC2 frontend verification is recorded in the integration PR.

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
