# Calendar intent source repair

Follow-up to merged #98, based on `9dadd558beb628bd7ef7d3e4d737e7f2f0c1f2d9`.
The authorized live acceptance run exposed a model protocol failure: for
“Hi, could you create an event called Threadly validation at 4pm in Validation
room?” the model omitted the greeting and punctuation from `intent.source`.
The backend correctly rejected that source, but returned the internal instruction
“Quote the complete top-level user directive for the intent” to the user and
retained no event draft.

The source equality check is unchanged. Its failure now becomes a typed error
returned to the model within the existing eight-call, 120-second loop, before
any state merge or action retirement. The prompt and field description explain
exact copying. An exhausted repair still reports that no booking was confirmed.
Release `contextual-conversation-1.8.1` pins the updated prompt/tool snapshot;
historical snapshots remain unchanged. No migration or configuration change.

Three PostgreSQL regressions failed on the merged baseline and pass with this
repair: truncated polite creation, correction preserving an existing preview
until the source is fixed, and bounded exhaustion without an event or draft.
The focused source/Calendar/provider diagnostics suite passed 119 tests, with no
failures or skips. Lint and the prompt snapshot check passed. Full backend and CI
results are recorded with the follow-up PR.

Authorized live evidence is in
[`live-intent-source-replay-2026-10-06.json`](../../evaluation/calendar-event-language/live-intent-source-replay-2026-10-06.json).
The candidate ran in an isolated process with copies of these source files,
the configured real model, actual Google Calendar reads, a six-model-call bound,
and process-local guards blocking event writes, approval and unrelated tools.
The running API files/settings were unchanged. The candidate retained title,
time and location through a read detour, resumed a missing day, replaced time,
cleared location and replayed the same request without extra provider calls.
The day-resume call needed two schema corrections before its valid tool call;
this evidence is one bounded replay, not a measured success rate.

Full live availability and Ask-preview acceptance remains blocked: the test
account's saved selections reference Google connection version 42 while its
current version is 43. The live list returned four calendars; free/busy correctly
returned `calendar_preferences_stale`, and event preparation required reviewing
Calendar settings. No selection, consent, scope or approval setting was changed.
No event, preview, approval, action job or action attempt was created. Temporary
validation conversations were removed. The owner must review/save the current
Calendar selection before those remaining acceptance checks can run.

Source review confirmed the exact equality check, creation authority, source
field validation, account/preference fences, immutable approval and dispatch
logic are preserved. Voice, quotas/chat deletion, email, workers, infrastructure
and frontend code are outside this change. This checkpoint does not claim the
candidate is merged/deployed or that a browser installation was validated.
