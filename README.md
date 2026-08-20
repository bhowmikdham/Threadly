# Threadly

AI-powered Gmail assistant: a Chrome side-panel extension backed by a self-hosted
FastAPI stack with local-first LLM inference. Built by Team DS-21 for FIT3163.
(The proposal report calls the product "MailMind" — the repo/product name is Threadly.)

## Repo map

| Path        | What it is                                                        | Owner          |
|-------------|-------------------------------------------------------------------|----------------|
| `frontend/` | Chrome extension (Plasmo, side panel UI)                          | Frontend team  |
| `backend/`  | The `api` container — FastAPI app, all server logic               | Backend        |
| `ml/`       | Artifact drop zone: classifier weights, QLoRA adapter, prompts    | AI team        |
| `infra/`    | Caddy config, EC2 deploy runbook                                  | Backend        |
| `docs/`     | Architecture, API contract, data model, decision records          | Everyone       |

One rule: **every box in `docs/architecture.md` has exactly one home in this tree.**
If you can't tell where code goes, the architecture doc decides.

## Services (docker compose)

| Service    | Image             | Role                                            |
|------------|-------------------|-------------------------------------------------|
| `caddy`    | caddy:2           | TLS termination, 443 -> api:8000, SSE passthrough |
| `api`      | build ./backend   | FastAPI — auth, sync, orchestration, RAG, voice |
| `postgres` | postgres:16       | 7 tables (see docs/data-model.md)               |
| `chroma`   | chromadb/chroma   | per-user sent-mail embeddings                   |

Inference does NOT run in this stack (see docs/decisions/001): primary is a Mac
running Ollama reached over Tailscale, fallback is OpenRouter.

## Quickstart (local dev)

```bash
cp .env.example .env        # fill in the blanks
make dev                    # api on http://localhost:8000 with hot reload (no caddy)
curl localhost:8000/healthz
```

Prod-shaped stack (caddy + TLS): `make up`. All targets: `make help` or read the Makefile.

## Where things stand

Backend build order (from the architecture doc):
W1 auth -> sync -> data layer -> model client -> /summary SSE ·
W2 cache, /threads, extractor, /entities ·
W3 RAG, /draft, voice+PII, planner ·
W4 drill, PII hardening, goldens, freeze.

## Docs to read first

1. `docs/architecture.md` — what runs where, and why
2. `docs/api-contract.md` — the frontend<->backend seam (contract-first: change via PR)
3. `docs/data-model.md` — tables, cache keys, uniqueness rules
