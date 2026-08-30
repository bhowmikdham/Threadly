# Threadly — Boundary Contracts

This is the document the frontend and AI teams build against. The backend owns
these contracts; changing anything here is a cross-team conversation.

## 1. Intents

The shared vocabulary of the whole system (`threadly_common.models.Intent`).
The planner emits one per chat message; the orchestrator dispatches on it.

| Intent | Meaning | Params | LLM involved? |
|---|---|---|---|
| `FETCH_ENTITY` | Structured lookup (order numbers, tracking, amounts) | `type`, `merchant?`, `window_days?` | **No** — pure SQL |
| `SUMMARISE` | Thread summary | `thread_id` from request | Yes, cache-first |
| `DRAFT` | Email generation | `instruction` | Yes, RAG + main model |
| `SEARCH` | Free-text question over mail | `query` | FTS first, LLM phrases the answer |
| `UNKNOWN` | Rules couldn't decide | — | Falls back to the classifier |

Structured data goes **around** the LLM, never through it.

## 2. Public API (gateway, behind Caddy on :443)

All `/v1/*` endpoints (except `/v1/auth/*`) require `Authorization: Bearer <access JWT>`.

```
POST /v1/auth/dev              {email}            # DEV_MODE only
GET  /v1/auth/google/url?state=                    -> {url} consent URL to open
POST /v1/auth/google/exchange  {code}
POST /v1/auth/refresh          {refresh_token}
POST /v1/chat                  {message, thread_id?}    -> SSE stream (§3)
GET  /v1/threads?limit&offset
GET  /v1/threads/{id}                                   -> thread + messages (incl. labels)
GET  /v1/messages?priority&action&category&limit&offset -> triage / priority inbox
GET  /v1/entities?type&merchant&window_days&cursor&limit -> §4
GET  /v1/commitments?status&direction&limit             -> asks + promises, by deadline
POST /v1/commitments/{id}/done
GET  /v1/drafts                / GET /v1/drafts/{id}
PATCH /v1/drafts/{id}          {subject?, body?, to_addrs?}
POST /v1/drafts/{id}/approve
POST /v1/drafts/{id}/send      header: Idempotency-Key (recommended)
POST /v1/drafts/{id}/discard
POST /v1/voice/stt             multipart audio     -> {text, stub}
POST /v1/voice/tts             {text, voice_id?}   -> audio stream (wav stub in dev)
POST /v1/sync                                           # manual sync trigger
GET  /healthz
```

`FETCH_ENTITY` with `type: commitment` reads the commitments table instead of
entities (asks vs promises, `direction: inbound|outbound`, ordered by `due_at`).

Errors (any non-2xx, "R18 envelope"):

```json
{"error": {"code": "illegal_transition", "message": "...", "request_id": "uuid", "details": {}}}
```

## 3. SSE events (`POST /v1/chat`)

`Content-Type: text/event-stream`. Every stream starts with `meta` and ends
with `done` or `error`. Frontend renders by event type and ignores unknown
types (forward compatibility).

| event | data | when |
|---|---|---|
| `meta` | `{request_id, intent, params, planner_source}` | always first |
| `token` | `{text}` | streamed prose (summary/draft/answer/status lines) |
| `entities` | `{items[], has_more, cursor, window_days, fuzzy}` | FETCH_ENTITY |
| `results` | `{items[], count}` | SEARCH hits (subject, from, snippet) |
| `summary` | `{thread_id, text, model, cached}` | SUMMARISE |
| `draft` | `{draft_id, status, to_addrs, subject, body, model}` | DRAFT — hand the user this draft card |
| `error` | R18 envelope | terminal |
| `done` | `{request_id}` | terminal |

### Ingestion normalization

Every synced message is normalized once, before storage: HTML-only bodies are
rendered to text (most order confirmations are HTML-only), and scraping
artifacts (forwarded-by banners, original-message markers, signatures) are
stripped. Everything downstream — extraction, FTS search, the classifiers,
RAG, summaries — consumes the cleaned text, which also keeps runtime input
shaped like the AI team's cleaned training data.

`merchant` (in chat and `GET /v1/entities`) is a fuzzy phrase, not an exact
value, matched in two tiers: substring/initialism first — "guzman y gomez",
"gyg" and "GYG" all find orders from `no-reply@em.gyg.com.au` — then, only
when that finds nothing, an edit-distance fallback for typos ("GIG" → GYG).
Responses set `fuzzy: true` when the fallback tier answered, so the UI can
show "closest match" phrasing.

## 4. Progressive disclosure (the "want more?" pattern)

First call returns the default window (60 days) plus `has_more` and an opaque
`cursor`. "Yes, show older" = repeat the call with `cursor=` (the window no
longer applies; the cursor keyset-paginates strictly backwards). The frontend
never parses the cursor.

## 5. Draft lifecycle

```
generated -> edited -> approved -> sending -> sent
     \          \          |            \-> failed -> (retry: sending)
      \-> discarded <------/
```

- Enforced server-side (`threadly_common.draft_state`); illegal moves are `409 illegal_transition`.
- **The LLM only ever creates rows in `generated`.** Only
  `POST /v1/drafts/{id}/send` — a user action behind approve — causes delivery.
- Sends are idempotent per `Idempotency-Key`: replays return the original result.

## 6. AI team hand-off: files, not services

Mounted at `/models` (see `models/README.md`):

- `classifiers/<task>/` — one HF sequence-classification dir per classifier:
  `priority/` (most important), `action/`, `category/`. Served by inference
  `/v1/classify {task}` and applied to **every inbound email at sync time**;
  results are stored on `messages` (`priority`, `action`, `category`, full
  detail in `labels` jsonb) and queryable via `GET /v1/messages?priority=high`.
  - **action labels (agreed)**: `approve, review, edit, complete_submit,
    attend, reply, no_action` — multiclass, exactly one per email. Label names
    come from the model's `config.json` id2label and are stored verbatim.
  - **priority / category labels**: whatever the model config declares; the
    backend treats them as opaque strings. Until models land, keyword-rule
    fallbacks emit `high|normal` and `purchases|scheduling|newsletters|work|other`.
  - **deadline stays out of the classifiers** (per AI team decision): the
    backend's commitments extractor owns deadlines (§ /v1/commitments).
- `bert/` — legacy single-dir location, still checked for the chat **intent**
  classifier (labels = intent names from §1); optional, the planner degrades
  to small-model JSON then rules without it.
- `prompts.yaml` — overrides the orchestrator's default templates. Slot names
  are the contract; the AI team owns the wording, the backend owns the slots:

| template | slots |
|---|---|
| `system` | — |
| `draft_email` | `{instruction}` `{thread_context}` `{rag_examples}` |
| `summarise_thread` | `{thread_text}` |
| `search_answer` | `{query}` `{snippets}` |

- `adapter/` — QLoRA adapter, applied on the Ollama host (never on AWS, rule R8).

## 7. Internal service APIs (compose network only)

Every internal call carries `X-Threadly-Internal: <token>`.

```
orchestrator POST /v1/orchestrate       {user_id, message, thread_id?} -> SSE
orchestrator POST /internal/rag/index   {user_id, docs: [{id, text}]}
inference    POST /v1/generate          {prompt, system?, max_tokens?, small_model?} -> SSE
inference    POST /v1/classify          {text, task?, labels?} -> {label, confidence, source: bert|llm|rules}
                                        task: intent (default) | priority | action | category
inference    POST /v1/embed             {texts[]} -> {embeddings[][], backend, dim}
gmail        POST /internal/oauth/exchange {code} -> {user_id, email}
gmail        POST /internal/send        {draft_id, user_id} -> {gmail_msg_id}
gmail        POST /internal/sync/trigger {user_id?} -> {results[]}
```

Model routing (inference service): Ollama (Mac over Tailscale) primary with a
2s health check cached 30s → OpenRouter fallback (prompts PII-masked on that
egress) → deterministic stub in dev mode. Thinking off, `max_tokens` capped.

## 8. Data ownership (single postgres, logical boundaries)

| tables | owner (writes) | notes |
|---|---|---|
| `users` | gateway (dev), gmail (oauth) | |
| `oauth_tokens` | gmail | Fernet-encrypted at rest |
| `threads`, `messages`, `entities`, `commitments` | gmail (sync/extractor) | others read |
| `summaries` | orchestrator | cache key `(thread_id, last_msg_id)` |
| `drafts` | orchestrator creates; gateway transitions | state machine in §5 |

Chroma: one collection per user (`user_{id}_sent`), written by the
orchestrator's RAG module, fed by the gmail service at sync time.
