# Calendar spoken requests and interrupted-turn recovery — working checkpoint

Status: in review; local verification complete on `codex/calendar-spoken-time`.
Original base `8a9e72cc049013d5f4d8dc1ac596d2e3ed173fe5`; now integrates merged
shared-context PR #89 at `41eeaa8c7639d3a0bb60552cfac613322362d816`. Draft review only. No new deployment,
Google consent, or real event/invitation work is part of this follow-up.

## Reproductions and causes

- `could you please book 2 p.m. tomorrow for doctor's appointment?`: dotted meridiem
  unsupported in parse_clock; creation authority required title before time.
- `book 2 pm tmrw for doctors appointment`: time-first request rejected, reaching
  generic tool-loop exhaustion after retries.
- `can you book me 2 pm for tmrw for doctors appointment`: additional `for` before
  date must preserve the same literal title/time/day rather than return a prose retry.
- `respond` accepted generic completion/progress promises without an event candidate.
- Explicit empty model defaults on a pending event follow-up could erase saved slots.
- A completed tool-limit failure retains an issued pending ID unless the service
  receives a terminal response; older clients discarded its exact retry body.

## Frontend integration contract (implemented and verified)

1. A bounded Calendar preparation exhaustion now returns HTTP 200, `kind=message`,
   `error_code=calendar_event_not_prepared`, conversation ID and incremented version.
   This completes the turn with no candidate/action/job. It is not an expired chat.
   Subsequent questions use that returned version; original-key retries replay its
   terminal receipt. Provider/unknown-outcome errors retain existing retry fencing.
2. `POST /assistant/conversations/{id}/recover` accepts
   `{pending_request_id, expected_version, operation: "recover"|"cancel"}`.
   Use the pending ID/version from the owned conversation GET; never invent an ID.
   GET additionally returns `pending_recovery: null | {active, has_saved_result}`.
   These are observations, not authorization: POST always rechecks the live lease,
   linked work and version. A fail-closed two-step UI also works without the hint:
   explicit Recover, then offer explicit Cancel only on `conversation_result_unavailable`.
   Neither GET nor client inference can establish `can_cancel`.
3. `recover` finalizes an already checkpointed response and returns its hydrated,
   current owned task/action data plus `recovered_request_id` and incremented version.
   It does not approve, execute, or cancel provider work. Its history entry has
   `recovered=true` and `user=""`; show only the assistant result/card.
4. With no saved result, `recover` returns 409 `conversation_result_unavailable`.
   Offer explicit cancellation of the unfinished request or exact-body retry.
5. `cancel` is allowed only with expired/released lease, matching pending ID/version,
   no saved result and no linked task/proposal/input/edit/action for the issued key.
   It records an exact-hash cancellation receipt before clearing pending fields;
   late old workers cannot checkpoint and their transaction rolls back. This is
   request cancellation, not Calendar event cancellation.
6. 409 `conversation_busy` waits; `conversation_version_conflict` or
   `conversation_pending_changed` reloads state; `conversation_result_available`
   requires recovery instead of cancellation; `conversation_work_exists` preserves
   work for review. No blind clear of pending IDs or unknown provider outcomes.

The new read-only `check_time_availability` tool answers the exact subsequent question
`am i free at 2 pm tmrw?`. It preserves date/time sources, uses and displays the saved
meeting duration (or explicit duration), and reads busy coverage for that interval.
It does not invent an end time, widen to a whole day or claim incomplete coverage is free.

## Version/integration notes

The original follow-up snapshot `contextual-conversation-1.5.1` is retained. The
current combined release is `contextual-conversation-1.7.2`, preserving shared mail
context 1.7.0 plus this Calendar follow-up. Direct creation policy is
`direct-calendar-event-1.1.0`; timed reads are `calendar-conversation-reads-2.1.0`.
Historical 1.5.0, 1.5.1, 1.6.0, 1.7.0 and 1.7.1 assets remain unchanged. Combined replay and
fresh verification are required; no edits were made to another chat's checkout.

## Prior release authorization (historical only)

The original human messages in this conversation explicitly said `yes deploy` and
`deploy the code`; AWS refresh was explicitly answered `Refresh AWS session`.
The approval-mode answer was `Offer both modes`. Those were the relied-on human
instructions for the completed #87 release; #88 was observed already merged by
bhowmikdham, then its successful publication was verified. Coordinator messages were
not used as release authority. This follow-up remains draft-only.

## Verification

Initial focused language/creation/read/follow-up/recovery pass: 196 passed, no skips,
disposable PostgreSQL, fake model/provider. The old-base full run was deliberately
interrupted after #89 merged; it is not counted as a complete pass. The final combined run and status-supersession regressions passed; see the final verification below. No live model-quality or live Google success is claimed for this follow-up.
A separate human-created canonical-order event was reported verified; it is untouched.

## Clickable Calendar choice contract — implemented and verified

A Calendar creation clarification/owned conversation GET adds nullable
`calendar_choices: {choices: [{choice_id: UUID, label: string, access: "editable"}], expires_at: ISO8601}`.
Only current selected calendars with event-write ACL are offered; order is authoritative.
Use the surrounding conversation `version`, not label text, for clicks. No provider
calendar ID is exposed in the choice payload. GET choices are hints; selection rechecks.

`POST /assistant/conversations/{conversation_id}/calendar-choice` accepts
`{request_id: UUID, expected_version: integer, choice_id: UUID}`. Retain/retry this exact
body on transport failure. The backend resumes the pending event without a model call,
preserving supplied fields and validating the issued choice against chat/account,
expiry, preferences and current ACL. Ordinary turn version/busy/retry conflicts remain
409; existing recovery can recover a checkpointed selection result with the issued ID.

Success is the ordinary versioned conversation response: `kind: calendar_event` plus
`calendar_action`/ID when complete, or `kind: clarification` for remaining fields.
Unavailable/expired choices complete with `error_code: calendar_choice_unavailable`;
changed preferences/options complete with `calendar_choices_changed` and fresh choices
where available. These are terminal HTTP 200 responses with the new conversation version;
no action is created until a valid choice is resolved. Repeated same-key clicks replay
one result. Ask still requires separate exact-payload approval; Always applies only the
already saved chat grant. Selection itself does not switch approval modes.

Display recovered results as assistant-only. The client must not turn a chosen label
into a new chat instruction, select automatically, or reinterpret an ordinal itself.


## Additional reported multi-turn reproduction

- After unsupported deletion, `could you create Meeting at4pm` → `tomorrow` →
  calendar email or `3rd one` previously drifted into reads/listing and lost state.
- Compact `at4pm` violated the literal clock boundary. Missing-day title-first requests
  were rejected before retaining authority; later added date words were incorrectly
  required in the original leading line. Creation now freezes that original authority,
  retains independently source-bound fields, and asks only the missing field.
- A read tool selected for a creation reply is now rejected with typed preparation
  feedback. Explicit calendar discovery is a supported detour and retains state.
- Display order is stored; email/ID lookup is limited to owned eligible calendars.
  Editable ACL is not labelled busy-only. Clicks bypass inference, preserve a chosen
  destination through missing-field replies, and checkpoint clarification results.
- Review covers quoted ordinal mentions, stale/reordered/duplicate/cross-chat choices,
  duplicate clicks, cancellation, old-success/new-failure status and late-commit fencing.

Focused combined 1.7.2 regression before the final quoted-selection guard: 222 passed,
zero skipped, 67.81 seconds. Earlier combined 1.7.1 full suite: 1,754 passed, zero
skipped, 554.58 seconds. That full result precedes the picker. The final 1.7.2 full run passed **1,774 tests,
zero failed/skipped**, in 550.98 seconds (two existing dependency deprecation warnings). No model/provider
live quality result is implied by these synthetic tests.


## Final verification and handoff

- Runtime commit: `2c9f5de7d79d3011af4056149d70106f5cfd2cbf` on
  `codex/calendar-spoken-time`; integration base `41eeaa8c7639d3a0bb60552cfac613322362d816`.
- Final full backend: 1,774 passed, 0 failed, 0 skipped, isolated PostgreSQL 16.
- Ruff `app tests tools` (no-cache), current MVP asset check, planning validator,
  backend handoff/card validators and `git diff --check` passed.
- [Versioned replay/evidence](../../evaluation/calendar-event-language/README.md)
  records current prompt/tool hashes and the exact focused case IDs.
- No migration/configuration change, deployment, scope change or real provider write.
  No live model-quality evaluation was run; fake-model results do not claim that gate.
- Matching clickable frontend: draft [PR #93](https://github.com/bhowmikdham/Threadly/pull/93)
  at `7cdd8df3240e222f0594635dfac63f26640cb469`; its extension CI was verified successful.
  The earlier recovery UI PR #91 was independently merged by its owner.
- Next gate: review the backend draft and paired picker integration before release.
  Existing queued actions keep their original payload/version/approval.


## Post-review exact phrase (6 October, 00:17 Melbourne)

`create me a event at 4pm tmrw for a meeting with kelly` is now a replayable regression.
At anchor `2026-10-05T13:17Z`, a wrong model `find_busy_times` call is rejected and the
prepared event is `meeting with kelly`, 7 October 16:00 Melbourne, no attendee email,
`send_updates=none`, proposed/Ask, no action job and no Google event insertion.
**1 additional targeted test passed**, no failed/skipped, in 1.27 seconds. No runtime,
prompt or tool change was needed. Preserve the prior 1,774 full-suite result as the
previous run; it excludes this added test. See
[the additional evidence](../../evaluation/calendar-event-language/reported-phrase-kelly.json).
The installed package/server version was not confirmed and no deployment occurred.
Draft review is [PR #94](https://github.com/bhowmikdham/Threadly/pull/94).


## 1.7.3 review follow-up (6 October Melbourne)

- Reproduced two expiry-during-list failures (`None.get` in destination resolution)
  under Ask and Always; return `calendar_choice_unavailable` with no action/job and
  replayable completion when expiry occurs during the read.
- Kelly replay used inconsistent clocks and failed once real time passed its
  frozen expiry. It now freezes store, creation, choices and guard clocks together.
- Reproduced both exact Ashu forms falling through to prose because “could you
  help me create” was not recognized. Direct policy `1.1.1` extends only that
  leading request prefix; content, quote, negative and permission guards remain.
- Replay retains 18:00 on 6 October Melbourne, literal `meeting with Ashu`, no
  inferred email/invitation, and Ask through three synthetic 429s and a retry of
  the same request. A fully specified request returns a proposed event rather
  than asking for an unnecessary attendee address.
- Release `contextual-conversation-1.7.3` has a new immutable asset snapshot;
  historical 1.7.2 assets/evidence remain. No prompt/tool schema, migration,
  Google scope, provider retry-policy or write-worker change.
- Focused affected suite: **215 passed, 0 failed/skipped**, 78.25 seconds on
  isolated PostgreSQL 16, synthetic Google and scripted model responses.
- Final full-suite/exact-head CI results are recorded in PR #94, separate from
  the previous head's green 1,775-test run. Local evidence:
  `/tmp/threadly-calendar-173-focused.xml` and `/tmp/threadly-calendar-173-full.xml`.
- Independent precision repair PR #96 is unchanged at `7a590d9`; its exact-head
  CI passed with 1,692 tests. No merge, deployment or real event operation occurred.
- The observed production Bedrock 429s are a separate operational limitation;
  these synthetic retry tests do not claim live model availability was restored.
