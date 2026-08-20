# backend/ — the `api` container

FastAPI app. One folder per box in the architecture diagram — if you're unsure
where code goes, `docs/architecture.md` has the module map (box #, folder, week).

## Layout

```
app/
├── main.py            app factory, router registration, error handlers
├── config.py          all settings, read from env (.env at repo root)
├── api/               1  routes/, JWT dep, error envelope (R18)
├── auth/              2  oauth exchange/refresh, Fernet token crypto
├── sync/              3  gmail client + sync worker (paginate ALL pages)
├── planner/           4  rules-first intent planning, 2b JSON fallback
├── orchestrator/      5  dispatch by intent — structured data AROUND the LLM
├── extractor/         6  tier1 regex, tier2 LLM residue, dedupe
├── rag/               7  sent-mail embeddings in chroma, retrieve w/ char cap
├── model_client/      8  ollama(mac) -> openrouter fallback chain
├── voice/             9  ElevenLabs STT/TTS proxy (keys server-side)
├── pii/               9  masking middleware for ALL cloud egress
├── db/                engine/session, 7 table models, repositories
└── schemas/           pydantic contracts — mirror docs/api-contract.md
```

## Run

From the repo root: `make dev` (docker) — or bare-metal:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # add heavy SDKs later as modules need them
uvicorn app.main:app --reload    # http://localhost:8000/healthz
```

## Conventions

- Every non-2xx response uses the error envelope — raise `ApiError`, never bare HTTPException.
- Heavy SDK imports (google, chromadb) stay INSIDE functions — startup and tests must not need them.
- Endpoints not built yet return 501 `not_implemented`, so the frontend can integrate against real shapes early.
- Schema change = model change + `make db-revision m="..."` + `docs/data-model.md` update, one PR.

## Tests

`make test` from the root, or `pytest -q` here. Golden tests for deterministic
paths live in `tests/goldens/` (W4 freeze gate).
