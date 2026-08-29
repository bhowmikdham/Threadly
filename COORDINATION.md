# Threadly — Agent Coordination Log

Coordinator session: `threadly-2c` (branch `claude/agent-coordination-review-drrqah`)
Worker session: "Threadly LLM/BERT service architecture" (branch `claude/threadly-llm-bert-architecture-btzyux`)

## Coordination protocol

- Coordinator → worker: instructions are delivered into the worker session via a
  bound trigger channel (`trig_011wk1iddQHVKyUZrovcYmM8`).
- Worker → coordinator: the worker cannot message back. It reports by pushing
  commits plus a `STATUS.md` (Done / In progress / Blocked / Questions) to its
  branch on origin. The coordinator reviews origin and sends the next round.
- The worker works only on its own branch; `main` and `frontend` are not to be
  pushed to by the worker.

## Repo state at coordination start (2026-08-27)

- `main` (a25ef9e): empty tree after cleanup commits.
- `frontend` (07968d7): Plasmo MV3 Chrome extension ("Mail Mind" / Threadly).
  Side panel with Google sign-in (`chrome.identity.getAuthToken`, scopes
  `userinfo.email` + `gmail.readonly`) and a placeholder chat input.
- Worker branch: not yet pushed to origin at coordination start.

## Review — `frontend` branch (07968d7)

1. **Sign-in is not actually persistent.** `signedIn`/`email` live only in React
   state in `sidepanel.tsx`. Closing and reopening the side panel loses the
   session even though the commit message says persistent sign-in is complete.
   Fix: on mount, call `chrome.identity.getAuthToken({ interactive: false })`
   and restore the signed-in state silently when a token exists.
2. **No error surface on sign-in.** Failures only hit `console.error`; the user
   sees a button that stops loading. `res.json()` from the userinfo call is
   also unchecked.
3. **Chat input is a stub.** `handleSendMessage` only `console.log`s — it needs
   the backend `/v1/chat` endpoint (assigned to the worker's architecture).
4. **`host_permissions: ["https://*/*"]` is over-broad.** Chrome Web Store
   review will flag it. Narrow to the actual API origins (googleapis.com and
   the future backend origin).
5. **`popup.tsx` is untouched Plasmo boilerplate.** Either remove it (the action
   already opens the side panel) or replace with a minimal launcher.
6. Minor: SVG namespace typo `xmlns="http://w3.org/2000/svg"` (missing `www.`);
   package metadata still says "A basic Plasmo extension" by "Plasmo Corp".

## Instruction rounds

### Round 1 — 2026-08-27

Sent to worker:
1. Push its branch to origin immediately and after every chunk (ephemeral
   container; coordinator reviews only origin).
2. Create and maintain `STATUS.md` at the branch root as the reporting channel.
3. Main deliverable `docs/ARCHITECTURE.md`: service topology (gateway / BERT
   classifier / LLM via Claude API, with an explicit BERT-vs-LLM division of
   labor); versioned `/v1` API contract with JSON schemas (token verification,
   email analyze/classify, chat); auth model (Google access token as Bearer,
   verified server-side, defined 401/expiry behavior, no data at rest without
   justification); Gmail data-flow decision (client-side vs server-side fetch)
   with recommendation; BERT model/serving specifics; deployment sketch;
   milestones M1 doc → M2 skeleton+tests → M3 chat wired to Claude API →
   M4 real classifier.

### Round 2 — 2026-08-27 (~13:35 UTC)

GitHub write access is fixed (this branch is now on origin). Worker pushed
`claude/threadly-llm-bert-architecture-btzyux` at 29878da: a docker-compose
microservice backend (Caddy → gateway → orchestrator → inference/gmail,
postgres + chroma), `contracts/CONTRACTS.md` boundary spec, CI, and unit tests.

Coordinator verification of 29878da:
- `python -m compileall` clean; **27/27 unit tests pass** (run locally, same as CI).
- Internal `X-Threadly-Internal` token enforced by all three internal services.
- Draft lifecycle FSM server-side, send user-gated + idempotent.
- Google tokens Fernet-encrypted at rest; only Caddy publishes ports.
- All `/v1` routes JWT-gated (incl. voice/sync/chat).

Findings sent back as round-2 instructions:
1. `STATUS.md` (round-1 ask) still missing — required as the reporting channel.
2. The architecture doc README references is not committed → `docs/ARCHITECTURE.md`.
3. LLM provider: worker built Ollama-primary + OpenRouter-fallback (inherited
   design) instead of the Claude API suggestion — accepted, but must be
   recorded as an explicit decision in the architecture doc.
4. **Frontend/backend auth mismatch (top priority):** the extension uses
   `chrome.identity.getAuthToken` (raw access token, `gmail.readonly` only) but
   the backend expects an OAuth *code* via `/v1/auth/google/url` + `exchange`
   with `gmail.send` in scope. Worker to write `docs/FRONTEND_INTEGRATION.md`
   (launchWebAuthFlow + `<ext-id>.chromiumapp.org` redirect, JWT storage +
   refresh, manifest changes, SSE consumption example) and add gateway
   CORSMiddleware for the extension origin.
5. Prod safety guard: fail fast when `DEV_MODE=false` with change-me
   secrets/empty Fernet key.
6. Refresh tokens are stateless (no rotation/revocation) — document as accepted
   MVP risk. Nit: `google_exchange` assumes JSON error bodies.
7. Verify the CI Actions run goes green on the worker branch.

### Round 3 — 2026-08-27 (~14:45 UTC)

- No new commits on the worker branch since 29878da; the worker session has
  been idle/disconnected since 13:17 UTC and does not appear to have processed
  the round-2 message (fired 13:38).
- Coordinator confirmed **GitHub Actions CI is green** on the worker branch
  (run #1, success, head 29878da).
- Re-fired the channel with a consolidated round-3 message (same work list as
  round 2: STATUS.md, docs/ARCHITECTURE.md + provider ADR + refresh-token risk
  note, docs/FRONTEND_INTEGRATION.md as top priority, gateway CORS, prod
  fail-fast guard, google_exchange JSON guard), marked safe against duplicate
  delivery.
- Probing whether the fire wakes the disconnected session; if not, the trigger
  channel only delivers when the worker session is reopened (it originates
  from the desktop app), and the user may need to reopen it — or hand the
  round-2/3 work list to another session.

Probe result: trigger fires do NOT wake the disconnected worker session
(updated_at unchanged through two fires). Rounds 2–3 are queued and will
deliver when the user next opens that session in the desktop app. Coordination
is blocked on that user action; check-ins continue meanwhile.

### Round 4 — 2026-08-27 (~16:05 UTC)

No change: no new commits on the worker branch, worker session still idle and
disconnected since 13:17 UTC (second consecutive unreachable check-in). Rounds
2–3 remain queued for delivery when the session is reopened. No re-fire (would
only duplicate the queue). Loop parked at a ~3-hour cadence until the worker
session is reopened or the user redirects the work.

Awaiting: worker session reopen → round-2+3 deliverables.

### Round 5 — 2026-08-28 (~04:20 UTC)

Worker woke at 03:08 UTC (user typed into its session) and pushed 99116c5:
tier-2 LLM entity extraction with hallucination guards, per-user rate limit on
/v1/chat, X-Request-ID tracing across services, DEPLOY.md runbook, 7 new tests.

Coordinator verification of 99116c5: compile clean, **34/34 tests pass**
locally, **CI green** (run #2). Quality remains high.

**Channel finding:** none of the round-2/3 items appear (no STATUS.md, no
docs/, no CORS, no fail-fast, google_exchange still unguarded) — the worker is
following its own build order and has evidently never received any coordinator
message. The trigger channel queues but does not deliver into this
desktop-app-bound session. Coordination now relays through the user.

**Relay text (user: paste into the worker session):**

> Coordinator instructions (relayed). Before further backend modules, ship the
> integration items: (1) STATUS.md at branch root (Done/In progress/Blocked/
> Questions), updated every push. (2) docs/FRONTEND_INTEGRATION.md — the
> extension on origin/frontend uses chrome.identity.getAuthToken
> (gmail.readonly only) and cannot produce the {code} that
> POST /v1/auth/google/exchange expects; spec the switch to
> chrome.identity.launchWebAuthFlow via GET /v1/auth/google/url with redirect
> https://<EXT_ID>.chromiumapp.org/, code exchange, JWT storage in
> chrome.storage.local + silent refresh, manifest changes (drop the oauth2
> block, narrow host_permissions), and a fetch+ReadableStream SSE example for
> POST /v1/chat. (3) Add gateway CORSMiddleware for the extension origin.
> (4) Commit the architecture doc as docs/ARCHITECTURE.md with an ADR for
> Ollama+OpenRouter vs Claude API and the stateless-refresh-JWT accepted risk.
> (5) Fail fast at startup when THREADLY_DEV_MODE=false and secrets are
> change-me defaults or the Fernet key is empty. (6) Guard google_exchange
> resp.json() on non-JSON 5xx. FYI: coordinator verified 99116c5 — 34/34
> tests, CI green.

### Round 6 — 2026-08-29 (~00:00 UTC)

Worker pushed b6ff2f1 (22:35 UTC): per-email action/category/priority
labeling pipeline aligned with the AI team's classifiers, GET /v1/messages
triage endpoint, per-user Chroma RAG cap with eviction, committed E2E suite
wired into CI as a compose-stack job.

Coordinator verification: compile clean, **41/41 unit tests pass** locally —
but **CI run #3 is RED**: the new e2e job failed.

**Root cause (from job logs):** `.env.example` carries inline comments after
values (`THREADLY_DEV_MODE=true   # ...`, `OPENROUTER_API_KEY=   # ...`). The
e2e job does `cp .env.example .env` and services load it via `env_file:
.env`; in the CI compose parser the comment text leaks into the values.
Evidence: inference logged "ollama unhealthy; falling back" and a real
OpenRouter call returning **401 Unauthorized**, though both vars should be
empty; `THREADLY_DEV_MODE` equally fails its `== "true"` check, so dev auth
404s and the smoke suite dies at login. **Fix:** move every inline comment to
its own line (optionally also strip values in config parsing), push, confirm
run #4 green.

Diagnosis sent through the channel (round 6) and relayed here for the user.
Round-2/3 integration deliverables remain outstanding (still no STATUS.md or
docs/ on the worker branch).

### Round 7 — 2026-08-29 (~01:55 UTC)

The worker applied the coordinator's exact diagnosis: 983c8da moves all
.env.example inline comments onto their own lines (single-file, disciplined
commit citing the 401 evidence) — **CI run #4 is green**. The channel/relay
reached the worker this time. Worker session is connected and active, now
asking its user for a go-ahead on "HTML extraction & merchant matching
fixes" for GYG-order search quality.

Round 7 sent: go-ahead granted for the HTML/merchant work, with ordering —
STATUS.md first (two-minute receipt), then the HTML/merchant fixes, then the
still-outstanding integration items 2–6 (FRONTEND_INTEGRATION.md, gateway
CORS, ARCHITECTURE.md + ADR, prod fail-fast, google_exchange guard), which
block the frontend team.

### Round 8 — 2026-08-29 (~06:30 UTC)

Worker delivered the green-lit work: 763c381 "Add ingestion normalization
and fuzzy merchant resolution" — sync-time HTML→text normalization matching
the AI team's training cleaning (explicit train/serve-skew avoidance),
mail-infrastructure subdomain parsing, initialism/phrase merchant matching.

Coordinator verification: compile clean, **50/50 unit tests pass** locally,
**CI run #5 green** including the compose E2E job (47s).

Still missing after six rounds of asks: STATUS.md and every docs/ deliverable.
Round 8 narrowed to a single focused ask — STATUS.md + docs/
FRONTEND_INTEGRATION.md (+ gateway CORS while in there) — with the full
launchWebAuthFlow/SSE spec restated; ARCHITECTURE.md, fail-fast, and the
google_exchange guard queued behind them.
