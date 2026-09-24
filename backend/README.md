# backend/ — the `api` container

FastAPI app for the authenticated conversation, on-demand Gmail, Calendar and
reviewed-action services. Use `docs/architecture.md` for the current topology and
`docs/contextual-conversation.md` for the feature-gated conversation contract.

## Layout

```
app/
├── main.py            app factory, router registration and error handlers
├── config.py          typed environment settings and safe defaults
├── api/               authenticated REST/SSE routes and error envelope
├── auth/              Google OAuth, Fernet token encryption and session JWT
├── conversation/      encrypted bounded dialogue, Bedrock tool loop and evaluation
├── assistant/         durable tasks, workflow proposals, source references and worker
├── actions/           exact-payload approval, execution and reconciliation worker
├── calendar/          preferences, free/busy reads, slots and event-action support
├── mail/              live bounded Gmail reads/search and write adapters
├── capabilities/      granted-scope and server-control readiness
├── workflows/         versioned native/auxiliary workflow assets and registry
├── model_client/      Bedrock adapters plus isolated legacy compatibility clients
├── pii/               reversible direct-identifier masking for model transport
├── db/                SQLAlchemy models and session lifecycle
├── schemas/           strict API/model contracts mirrored in docs/api-contract.md
└── sync/              retired mailbox-sync compatibility code; not normal staging
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
- Keep Gmail/Calendar provider calls outside database transactions and hydrate source
  references only within request/attempt scope.
- Schema change = model change + `make db-revision m="..."` + `docs/data-model.md` update, one PR.

## Current status

Implemented source includes OAuth/capability handling, live on-demand Gmail reads,
durable summary/draft/plan/scheduling workflows, exact email/Calendar action review,
workers and the feature-gated Bedrock conversation coordinator. Mailbox import is
retired in staging. `CONVERSATION_ENABLED` defaults false; enabling it requires the
reviewed Haiku inference-profile configuration and an operator cloud-processing
attestation. External email and Calendar writes remain controlled by separate server
flags, pilot allowlists, exact approval and reconciliation. Source presence or a healthy
container is not evidence that the live provider/evaluation gates passed.

## Tests

`make test` from the root, or `pytest -q` here. Golden tests for deterministic
paths live in `tests/goldens/` (W4 freeze gate).
