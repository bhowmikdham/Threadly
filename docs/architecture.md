# Architecture — what runs where

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
write client or approval executor is installed. See [draft lifecycle](assistant-drafts.md).

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
