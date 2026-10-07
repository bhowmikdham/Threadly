# Second diagnostic: specific improvement, remaining behavioral failures

**Stopped after the three targeted cases: 9 / 18 additional attempts used, all
nine provider responses completed; 9 unused.** No throttle or preflight failure.
Prompt `.3` was unchanged throughout. No third run, release, production change,
real provider write, approval or action job occurred.

Usage: **243,954 input / 1,059 output tokens**. Estimated cost at the already
verified Sydney prices: **USD 0.27417390 before tax; USD 0.30159129 allowing 10% GST**.
This is calculated usage, not an invoice. Across both batches: 27 attempts, 26
completed responses, one first-batch throttle; USD 0.86728323 completed estimate
including GST, or USD 0.91689323 with the first failed attempt fully reserved.
The first ledger and its evidence hashes are unchanged.

The second paid window was **2026-10-07 12:07:26–12:08:53 UTC**. The persistent
batch deadline for starting further calls is **12:22:26 UTC**. Unused attempts do
not reset that deadline or authorize a third batch. Execution stopped after the
planned cases established the failures, rather than spending the remaining
allowance on repeated versions of the same observation.

## Actual behavior and comparison

| Case | Attempts | Observed behavior | Review |
| --- | --- | --- | --- |
| Corrected drafting | 1–4 | Casey's new request included `request_source` and correctly asked only for purpose. The model selected Alex and supplied a complete useful draft in one preparation call. It then generated Casey's text without selecting Casey's existing goal. | The targeted `.3` contract guidance improved the first two failures. **Goal identity still failed:** Alex's current goal was overwritten/renamed Casey, while the original Casey goal remained separate and unfinished. Both visible texts were useful, but this is not successful independent-goal completion. |
| Calendar | 5–7 | Prepared Quiet hour tomorrow at 2 pm, revised it to 3 pm while preserving title/day, kept Ask mode and no execution. The gratitude reply then said to click **Create draft** to save it to Calendar. | Preview and revision passed. Closing guidance failed: wrong control name and an unsolicited action instruction after “Thanks, that's all.” The actual Calendar control is Create event. No claim of completed booking or actual write occurred. |
| Saved draft | 8–9 | Fresh-read the source, then called `review_email_draft`. Returned permission/save instructions. The saved task/artifact still existed, and Calendar remained focused. | **Restoration failed.** No `resume_conversation_task` or `select_conversation_goal`; response omitted the saved `task_id`/artifact payload. This is a wrong operation choice, not deleted data or unavailable credentials. |

The harness's `1 passed` means it completed and recorded isolation assertions. It
is not a model-quality pass. There is no overall clean acceptance result. This run
used disclosed scripted setup to focus on particular operations; it does not prove
an entirely model-driven source → generation → detour → restoration sequence.

## Proven cause and next correction

The goal overwrite is supported by both trace and state. After call 3, Alex's goal
`de457630-b1bb-481a-a42d-bdc62d672943` held the sapphire-crate draft. Call 4 supplied
`recipient=Casey`, `continue_previous=true` and new generated text without a goal
selection. `email_draft.prepare` validates that the new recipient appears in the
current user turn, then carries the previous `goal_id` into the modified payload
(`backend/app/conversation/email_draft.py`, lines 214–244 at runtime `744982f`).
`goals.checkpoint` correctly persists that same identity, overwriting the Alex goal's
current payload. Both retained labels became Casey. The original Casey goal ID
`00a21d21-dff4-4281-a2e2-3157a091a68b` stayed unfinished. Earlier Alex text remains in
the archived exchange; this is current-goal corruption, not deletion of all history.

A targeted correction should bind continuation to an explicit owned goal identity,
and reject ambiguous mutations before changing stored fields. Requiring selection
or a typed goal ID when multiple matching goals are retained would make the boundary
checkable without inferring intent from names or silently routing to a guessed goal.
Replay this exact wrong call and require an unchanged Alex goal, then explicit Casey
selection and completion. Also cover intentional recipient revisions so the guard
does not incorrectly forbid legitimate edits. A prompt reminder alone is insufficient.

For the saved-draft request, the valid task
`a685b69c-747c-4380-a8e3-2b900c478a9f` and artifact
`56550b6f-c6f9-4eb4-ab5c-77d54806616a` remained available. The model chose the status
review tool, whose backend correctly returned status/permission guidance. It did
not choose the restoration operation. A targeted follow-up should clearly distinguish
reopening existing work from asking to save it, and make returned goal/artifact focus
explicit in the tool/API contract. Verify the correct artifact is rendered and no
new worker job is created. Do not treat fresh source access as proof of restoration.

Calendar control guidance should come from the current action's real controls,
with simple acknowledgments on closing turns. Verify held-out gratitude and status
requests against Calendar and email cards separately. These are recommendations;
**the three newly observed behavioral defects are not fixed by this evidence commit**.
The `.3` runtime remains unchanged so its receipts stay attributable.

## Evidence and validation

The exact second harness and fixtures are committed at `a3dc5fc`; runtime/prompt `.3`
at `744982f` previously passed the full **2,264-test backend suite**, no skips. The
second harness/offline contracts passed **16 tests** with all AWS calls blocked.
The offline scripts correctly selected Casey and restored the saved draft; the live
model's different choices explain why those mechanical successes do not establish
semantic reliability. Fixture setup corrections are disclosed in the
[approved scope](second-stage-scope.md).

[Machine review](second-model-review.json) includes every request ID, per-call usage,
UTC timestamps, hashes and both-batch totals. [Scenario state and responses](second-scenarios.json)
include scripted setup separately. The [compressed synthetic request/response ledger](second-model-ledger.json.gz)
is replay evidence; no credentials or real mailbox history are included.
The separate first [results](approved-evaluation-status.md) remain available.
