# Calendar day-availability regression — 29 September 2026

Scope: fix the standalone chat request “can you tell me if i am free on the
thurday this week” and the existing date-confirmation → “yes” exchange.
This is a bounded correction on top of merged PR #68, not completion of a broader B task.

Base: integration `4f115549f9d1b3d2b6bdc2bb0e8ca0a99b06dec4`, tree
`09896869455479bd00d9f53be8f4b359cc44dbff`. The local source commit `5d092d7`
has that same tree. Work is isolated on `codex/chat-day-availability`.

The conversation engine now runs an exact, bounded self-availability read before
model inference. The server resolves the requested day in saved Calendar timezone,
uses current owner-scoped selections/freebusy, and returns checked busy periods or
an explicit unknown/access failure. A bare confirmation preserves the user request;
assistant-authored dates never determine the query. Whole-day questions need no
meeting duration. Today covers remaining hours. More complex scheduling stays with
the existing coordinator, and confirmations belonging to active typed work stay there.

Changed: day-read service, conversation dispatch/runtime/history persistence, version
manifest, regression tests/replay fixture, API and architecture docs. No migration,
new endpoint, OAuth grant or external-write path. Conversation release `1.2.4` pins
`calendar-day-answer-1.0.0`. The model prompt and model tool schemas are unchanged.

Verification:

- Full backend suite: **903 passed, 608 skipped**, one existing Starlette warning.
  Command: `python -m pytest -q` from `backend`, using the existing Python 3.12 test
  environment. The first sandboxed run had two loopback-bind permission failures;
  rerun with local socket access passed. PostgreSQL was unavailable, so the database
  suite is not verified locally.
- **33** new regression cases pass, including the versioned three-case replay through
  real Runtime/engine with fixture Calendar adapters and a model that must not run.
- Regression covers typo, weekday/date, confirmed follow-up, no assistant date trust,
  local week/DST, busy merging, unknown coverage, missing grants/settings, upstream
  failure, preference/account races, past dates, and active-task boundaries.
- Ruff check and format check pass for the changed Python files.
- Documentation plan validator and `git diff --check` pass.
- No live Bedrock evaluation: this route bypasses the model. No live Google request,
  real mailbox/calendar mutation, deployment or production acceptance test performed.

Next integration gate: CI PostgreSQL suite, then an authorized Calendar test-account
smoke through the extension after deployment. Existing Calendar preferences and read
consent are prerequisites. Do not infer that the running extension has this fix.
