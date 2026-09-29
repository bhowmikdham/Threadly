# Calendar agent tools — first read catalogue slice

Status: implemented and locally verified; live integration and deployment pending.
Base: `c7b29e0972f86a1b77e53cd129f98da552f61635` (merged backend PR 71).
Branch: `codex/calendar-agent-read-tools`.
Mapped scope: bounded extension of B12/B13 read services and B16 versioned tools;
not a declaration that those wider packages or the full connector are complete.

## Delivered behavior

- Five terminal conversational tools: list calendars, search/list events by literal
  words and date/window, busy intervals, free meeting slots, event overlaps.
- Free-slot questions return checked answers directly rather than proposal cards.
  Saved working hours, buffers, minimum notice and default duration are honoured.
- Literal user-bound date/time/search fields; no provider IDs or write arguments.
  Weekday/week/ISO ranges and paired clock bounds resolve in the saved timezone.
  Ambiguous clocks, DST gaps/folds, dropped qualifiers and excessive windows clarify.
- Existing owner/grant/ACL, account/preference-version and freshness checks remain
  at provider seams. Unknown calendars block free-slot suggestions, while verified
  busy intervals remain visible. Search truncation is explicit partial coverage.
- Private events remain redacted. Provider text is rendered directly and withheld
  from model history. Overlapping copies of a meeting are not labelled conflicts.
- Calendar date anchor persisted in encrypted turn state before model/provider
  calls, retained across crash retries. No migrations, new environment variables,
  AWS resources, external writes or additional scopes.
- Prompt/tool release `contextual-conversation-1.3.0`, read policy
  `calendar-conversation-reads-1.0.0`; complete 1.2.5/1.3.0 asset snapshots and replay
  fixtures in `docs/evaluation/calendar-agent-tools/`.

## Verification

An isolated PostgreSQL 16 container `threadly-calendar-tools-test-db` on local
port 55439 is used with `THREADLY_REQUIRE_TEST_DB=1`; no live mailbox database.

- Initial focused Calendar/conversation regression: **249 passed**, no skips.
- After adding retry anchor, route and history tests: **106 passed**, no skips.
- New cases exercise real Runtime/engine selection, actual Google httpx encoding,
  deterministic slot/overlap math, route → service → PostgreSQL, two-user ownership,
  preference race, crash retry and privacy filtering. Model decisions and Google
  responses are synthetic; these results do not measure live language quality.
- Ruff check passed; implementation-playbook and backend-handoff validators passed;
  `git diff --check` passed.
- Full PostgreSQL backend suite: **1,579 passed**, no skips (341.09 seconds).
- Final sequential Calendar/neighboring scheduling/session regression after the
  parameter-binding review: **144 passed**, no skips (21.53 seconds), including
  all **45** new read-tool tests and the six added after the full suite collected.
- Existing test dependency emits one Starlette/httpx deprecation warning.
- Live Bedrock/Google verification and deployment: not performed for this slice.

## Review and limitations

Self-review: traced schema dispatch, literal grounding, Google request boundaries,
partial coverage, interval calculations, version checks, encrypted receipts and
model-history filtering. No unrestricted write or new provider-ID input exists.
Review also tightened overlapping parameter source spans so a search query cannot
consume the date and hide an omitted qualifier. Old agenda/day tool schemas and queued workflow registry releases are preserved.
The historical `calendar_slots_use_schedule` evaluation case ID is retained, with
current expected behavior updated to a direct free-slot read; historical receipts
are unchanged and are not evidence for the new prompt.

Every tool currently finishes one read turn with a checked answer. Event search
is bounded to one 25-event page per calendar, 50 returned overall and 10 displayed;
users narrow dates to see more. No single-event handles/details or continuation
cursor yet. Free slots are suggestions, not durable booking offer IDs. The next
booking must recheck through the existing scheduling/approval path. Common
availability and rooms are explicitly unavailable. Existing event-write feature
controls remain unchanged.

## Next slices

1. Stable owner-bound event references, fresh event detail reads and bounded
   search continuation (same filters/versions across pages).
2. Participant/common availability through explicitly accessible calendars;
   room calendars only when actually granted by the connected account.
3. Exact-payload approved create/update/delete/RSVP, with event version checks,
   recurring-instance scope, provider outcome reconciliation and recovery tests.

Provider contract reviewed: [Google Calendar events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list).
