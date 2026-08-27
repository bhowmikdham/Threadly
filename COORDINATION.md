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

Awaiting: worker's branch push + STATUS.md. Next review scheduled ~45 min out.
