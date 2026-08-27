# Threadly

AI email assistant living in a Gmail side panel (Chrome extension). This repo
holds the **backend**: a docker-compose microservice stack that the frontend
team (extension) and AI team (models) plug into via the contracts in
[`contracts/CONTRACTS.md`](contracts/CONTRACTS.md).

## Topology

```
Chrome extension ──HTTPS/SSE──► caddy :443
                                  │
                            gateway :8000     auth · JWT · SSE proxy · draft
                                  │           lifecycle · entity pagination
                          orchestrator :8010  planner · intent dispatch ·
                            │     │    │      prompts · RAG · summary cache
                            │     │    └────► postgres :5432  (7 tables, FTS)
                            │     └─────────► chroma           (per-user sent-mail vectors)
                            ▼
                          inference :8020     model client: Ollama (Mac, primary)
                                              → OpenRouter (fallback, PII-masked)
                                              → stub (dev) · BERT classify · embeddings
                          gmail :8030         OAuth tokens (Fernet) · sync worker ·
                                              regex extractor · send (user-approved only)
```

Design rules inherited from the architecture doc:

- **Structured data goes around the LLM, never through it.** Order numbers etc.
  are extracted at sync time into `entities`; chat-time lookups are SQL.
- **The LLM never sends email.** Drafts move `generated → approved → sending →
  sent` and only a user-gated endpoint crosses `approved`.
- **Inference never runs on AWS.** The inference service is a router: Ollama on
  the Mac (Tailscale) primary, OpenRouter fallback with PII-masked prompts.
- **AI team ships files, not services** — dropped into `models/`, loaded by code.

## Quickstart (dev mode, zero credentials)

```bash
cp .env.example .env       # defaults are fine; set THREADLY_DEV_MODE=true
make up                    # builds + starts everything
```

Then exercise the full cycle:

```bash
# 1. login (dev mode) — returns a JWT
TOKEN=$(curl -s localhost:80/v1/auth/dev -H 'content-type: application/json' \
  -d '{"email":"me@example.com"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# 2. sync the (mock) inbox — GYG orders, tracking, an invoice, a real thread
curl -s -X POST localhost:80/v1/sync -H "Authorization: Bearer $TOKEN"

# 3. the GYG question — pure SQL, no LLM, with has_more/cursor
curl -sN localhost:80/v1/chat -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"message":"give me the order number of all the orders that i did for GYG"}'

# 4. draft an email (stub model in dev), then approve + send (mock Gmail)
curl -sN localhost:80/v1/chat -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"message":"draft a reply to Priya about the Q3 numbers"}'
curl -s -X POST localhost:80/v1/drafts/1/approve -H "Authorization: Bearer $TOKEN"
curl -s -X POST localhost:80/v1/drafts/1/send -H "Authorization: Bearer $TOKEN" -H 'Idempotency-Key: demo-1'
```

(Port 80 locally; in prod Caddy serves 443 with auto-TLS for `THREADLY_DOMAIN`.)

## Development

```bash
make test      # pure-logic unit tests (planner, state machine, cursor, extractor, PII)
make compile   # syntax check everything
make logs      # follow all services
make psql      # postgres shell
```

## Repo layout

```
contracts/CONTRACTS.md   the boundary spec: intents, SSE events, API, prompts slots
libs/common/             threadly-common: shared models, SSE framing, cursors, draft FSM
services/gateway/        public API, auth, draft lifecycle
services/orchestrator/   planner + dispatch + RAG + prompt assembly
services/inference/      model routing, classify (BERT-ready), embeddings, PII mask
services/gmail/          OAuth custody, sync worker, extractor, send
infra/                   Caddyfile, postgres schema
models/                  AI team drop zone (mounted read-only)
tests/                   unit tests for the pure logic
```

## Production notes

- EC2 t3.small+, security group 443+22 only, Elastic IP, billing alarm.
- Real Gmail: set `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI`, `THREADLY_FERNET_KEY`,
  and `THREADLY_DEV_MODE=false`; users land via `POST /v1/auth/google/exchange`.
- Real models: set `THREADLY_OLLAMA_BASE_URL` (Tailscale IP) and/or `OPENROUTER_API_KEY`.
- BERT classify: drop `models/bert/`, rebuild inference with `--build-arg WITH_BERT=1`.
