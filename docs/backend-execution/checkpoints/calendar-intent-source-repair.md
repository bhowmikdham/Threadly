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

The initial live availability and Ask-preview acceptance was blocked: the test
account's saved selections reference Google connection version 42 while its
current version is 43. The live list returned four calendars; free/busy correctly
returned `calendar_preferences_stale`, and event preparation required reviewing
Calendar settings. No selection, consent, scope or approval setting was changed.
No event, preview, approval, action job or action attempt was created. Temporary
validation conversations were removed. A later read observed matching current/saved account version 44. No agent changed
the saved selection; the subsequent bounded acceptance below could proceed.

Source review confirmed the exact equality check, creation authority, source
field validation, account/preference fences, immutable approval and dispatch
logic are preserved. Voice, quotas/chat deletion, email, workers, infrastructure
and frontend code are outside this change. This checkpoint does not claim the
candidate is merged/deployed or that a browser installation was validated.


## Voice review and final candidate

The user supplied a real cricket voice conversation. A read-only identity trace
showed one action, one approval and one successful dispatch. A narrow Google read
returned exactly one matching event in its 17:00–17:30 Melbourne window. Gratitude
replayed the same action card; it did not create another event. Local operational
evidence is retained outside the repository, without changing the actual event.

The updated candidate additionally accepts “make an event” through the same
bounded creation grammar, rejects unexecuted retry promises for that request,
returns plain acknowledgments for standalone gratitude/closing, and explains the
saved default duration in a proposed event. A fresh explicit creation cannot be
silently routed as a revision of an old draft. An unambiguous trailing title after
“for” (with date/time preceding it) must be complete; a shortened model value is
returned for bounded repair. Location, duration and later date clauses remain
separate, and existing “a meeting with …” phrasing is preserved. No payload write,
approval, permission or account fence is relaxed.

Conversation release `contextual-conversation-1.8.2` has its own snapshot; 1.8.1
and all prior snapshots remain preserved. The initial source-only commit passed
1,839 full local tests and exact-head CI. Expanded focused checks passed 163 before
the title guard; the title/contract suite passed 28 after the final compatibility
adjustment. The final full-suite result is in the PR and CI evidence.

[`live-voice-context-replay-2026-10-06.json`](../../evaluation/calendar-event-language/live-voice-context-replay-2026-10-06.json)
records the complete successful real-model/Google replay: availability coverage,
retained title/time/location over a read detour, day resume, revised Ask preview,
old-preview supersession, exact retry identity, gratitude including a garbled
spoken name, closing, new full title and saved-duration disclosure. This run used
12 model decisions and five Google reads. A subsequent exact-final-source check
used one model decision and one Google list read to verify article compatibility.
The comprehensive replay preceded only that article normalization; both source
hash sets are recorded honestly. The model still required bounded schema repairs;
this is acceptance evidence, not a measured model success rate.

All validation artifacts remained Ask. Temporary proposed previews were cancelled,
older previews superseded and validation chats removed. There were no approvals,
action jobs, action attempts, Google event writes or emails from these checks.
Live destination-picker selection was not exercised because the account had one
selected editable destination; the existing picker browser tests cover that path.
The source changes remain in the same draft PR until separately integrated.
Frontend speech recognition of the product name and real-browser installed-copy
verification remain separate limitations; this patch does not claim to fix ASR.
