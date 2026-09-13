# Architecture — what runs where

Source of truth for the system topology. Mirrors the "MailMind — Dockerised
Backend Topology" diagram from the project docs (the PDF lives in the team's
Claude project / drive). Every box below maps to exactly one folder in this repo.

## Topology

Implementation update (13 September 2026): [ADR 003](decisions/003-bedrock-migration.md)
supersedes the inference placement below. `INFERENCE_PROVIDER=bedrock` routes
generation through the new Bedrock Converse adapter. `legacy` retains the
original topology during migration. Durable assistant runs and Bedrock Flows
remain planned in the [implementation playbook](implementation-playbook/README.md).

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
  │                        7 tables        per-user            │
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
