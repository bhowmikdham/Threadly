# API contract — v0 DRAFT

The seam between `frontend/` and `backend/`. **Contract-first**: any change to a
path, field, or event shape happens by PR to this file, reviewed by both sides,
before the code changes. The pydantic models in `backend/app/schemas/` mirror this.

Status: v0.1 — W1 endpoints are LIVE: `/auth/google/exchange`, `/auth/refresh`,
`/healthz`, `/readyz`, `/sync`, `/threads`, `/threads/{id}`, `/threads/{id}/summary` (SSE).
Still stubbed (501): `/draft*`, `/entities`, `/commitments`, `/voice/*`.

Inference migration: uncached summaries support `INFERENCE_PROVIDER=bedrock`
([ADR 003](decisions/003-bedrock-migration.md)). The initial Bedrock adapter buffers
and validates the full response before emitting one `token` event, followed by
`done` with provider `bedrock`. Model failure emits terminal `error` with code
`upstream_model_unavailable`; no success event or cache write follows. Existing
cached results may still come from legacy inference.

## Conventions

- Base URL: `https://<DOMAIN>` (dev: `http://localhost:8000`)
- Auth: `Authorization: Bearer <session JWT>` on everything except `/healthz` and `/auth/*`
- Content type: JSON unless stated
- IDs: Gmail thread/message ids are passed as opaque strings

### Error envelope (R18)

Every non-2xx response, no exceptions:

```json
{ "error": { "code": "not_implemented", "message": "human-readable", "detail": null } }
```

`code` is a stable machine string (`unauthorized`, `not_found`, `validation_error`,
`upstream_model_unavailable`, ...). Frontend switches on `code`, never on `message`.

### SSE

Streaming endpoints use `text/event-stream`. Events:

```
event: token        data: {"text": "..."}          # incremental content
event: done         data: {"usage": {...}}          # terminal event, always sent
event: error        data: {"error": {envelope}}     # terminal on failure
```

## Endpoints

### Assistant intent preview (implemented; no workflow execution)

`POST /assistant/route-preview` requires the session JWT and accepts:

```json
{"instruction": "Summarise this thread", "intent_hint": null}
```

`instruction` is required, nonblank, maximum 8,000 characters. `intent_hint` is
optional and accepts the five canonical intents or null. Unknown fields are
rejected, including user IDs, context IDs and continuation objects. This initial
endpoint reads no mailbox or calendar state and persists no task or approval.

Returns HTTP 200 with `{decision, router_version, source, execution_ready: false}`.
`decision` follows the playbook route-decision contract; `source` is `rule|model`.
An exact summary command returns intent `summarise`, operation `summarise_thread`,
status `needs_clarification`, and missing field `source_context`. A known intent
does not mean an executable workflow exists. The frontend must not dispatch tools
or send messages from this response.

Simple exact commands avoid inference. Richer requests use the selected model's
small-model configuration, one bounded attempt and strict JSON validation. A hint
cannot override a compound request. The initial allowed combinations are summary,
work plan, availability check or slot suggestion followed by reply; entity/mail
lookup followed by reply or compose; commitment lookup followed by reply.
Unsupported or reversed sequences are rejected, not executed.

Errors: 401 missing/invalid session, 422 invalid request, 502
`invalid_route_output` for invalid model proposals, 503
`upstream_model_unavailable` for inference failure. Error details do not expose
raw model output. Request validation detail entries contain `loc`, `type`, `msg`.

The planned `POST /assistant/requests`, saved context, durable jobs, continuation,
artifacts and approval APIs are **not implemented by this preview endpoint**.
The typed `AssistantRequest` model exists for that next integration, but has no
registered route yet. Existing `/threads/{thread_id}/summary` remains the working
summary execution endpoint.

### Auth
| Method | Path                    | Body                    | Returns |
|--------|-------------------------|-------------------------|---------|
| POST   | `/auth/google/exchange` | `{"code": "...", "redirect_uri": "..."}` — auth code from `chrome.identity.launchWebAuthFlow`, plus the redirect URI used | `{"jwt": "...", "user": {"id", "email", "name"}}` |
| POST   | `/auth/refresh`         | — (valid JWT)           | `{"jwt": "..."}` |

### Health
| Method | Path       | Returns |
|--------|------------|---------|
| GET    | `/healthz` | `{"status": "ok", "version": "..."}` — liveness, no auth, no DB touch |
| GET    | `/readyz`  | `{"status": "ok", "postgres": true, "chroma": true}` — 503 + envelope if a dependency is down |

### Sync
| Method | Path    | Body | Returns |
|--------|---------|------|---------|
| POST   | `/sync` | —    | `{"mode": "backfill|incremental", "messages_upserted": n, "threads_touched": n}` — first call walks the whole mailbox (ALL pages), later calls use the Gmail history cursor; expired cursor transparently re-backfills |

Call it right after login, then on side-panel open. Runs inline in W1 (a few seconds for test inboxes).

### Threads
| Method | Path                      | Query                                   | Returns |
|--------|---------------------------|-----------------------------------------|---------|
| GET    | `/threads`                | `filter=needs_reply|all`, `page`, `page_size` | `{"threads": [ThreadOut], "next_page": int|null}` |
| GET    | `/threads/{thread_id}`    | —                                       | `ThreadOut` + messages |
| GET    | `/threads/{thread_id}/summary` | `Accept: text/event-stream`        | SSE stream; cached summaries emit one `token` then `done` |

### Drafting
| Method | Path      | Body | Returns |
|--------|-----------|------|---------|
| POST   | `/draft`  | `{"thread_id", "instruction", "tone": "match_my_voice|formal|brief"}` | SSE stream of the draft |
| POST   | `/draft/{draft_id}/send` | — | `{"sent": true, "gmail_msg_id"}` — only after explicit user approval in the UI |

### Entities & commitments
| Method | Path           | Query                        | Returns |
|--------|----------------|------------------------------|---------|
| GET    | `/entities`    | `type=` (optional)           | `{"entities": [EntityOut]}` — straight from postgres, no model call |
| GET    | `/commitments` | `status=open|done|all`       | `{"commitments": [CommitmentOut]}` |

### Voice
| Method | Path                | Body                  | Returns |
|--------|---------------------|-----------------------|---------|
| POST   | `/voice/transcribe` | multipart audio       | `{"text": "..."}` |
| POST   | `/voice/speak`      | `{"text": "..."}`     | audio stream (`audio/mpeg`) |

Voice keys never reach the extension; the api proxies ElevenLabs (module 9) and
masks PII before any cloud egress.

## Open questions (settle before W2)

- [x] Pagination for `/threads`: page numbers, `page_size` 25 (decided W1 — shout if the panel wants cursors)
- [ ] Does the side panel want thread list deltas pushed (SSE) or is poll-on-open fine?
- [ ] Draft approval flow: does `send` live in backend (`/draft/{id}/send`) or does the
      extension compose via Gmail UI with the draft text? Changes module 3 scope.
