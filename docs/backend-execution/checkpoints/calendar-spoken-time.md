# Calendar spoken requests and interrupted-turn recovery — working checkpoint

Status: integration verification in progress on `codex/calendar-spoken-time`.
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

## Frontend integration contract (implemented, tests in progress)

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
current combined release is `contextual-conversation-1.7.1`, preserving shared mail
context 1.7.0 plus this Calendar follow-up. Direct creation policy is
`direct-calendar-event-1.0.1`; timed reads are `calendar-conversation-reads-2.1.0`.
Historical 1.5.0, 1.5.1, 1.6.0 and 1.7.0 assets remain unchanged. Combined replay and
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
interrupted after #89 merged; it is not counted as a complete pass. A fresh combined
run and the final status-supersession regressions are in progress. No live model-quality or live Google success is claimed for this follow-up.
A separate human-created canonical-order event was reported verified; it is untouched.
