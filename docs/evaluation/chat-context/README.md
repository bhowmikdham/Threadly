# Shared chat-context verification

Current runtime release: **`contextual-conversation-1.8.9+chat-context.7`**.
The [offline goal-review correction](context7-goal-review.md) binds review to owned
goals, removes inapplicable Gmail controls, and covers mixed/closed-goal status.
The exact recorded failure is rejected and a scripted correction returns the
existing Calendar card. Full check evidence is in `context7-verification.json`.
The [final authorized Calendar diagnostic](context7-live-results.md) subsequently
passed both follow-ups in one live call each. The third ledger is now exhausted
at **12/12, USD 0.41798603 including GST**. The user authorized deployment after
this bounded acceptance; publication/cutover evidence is tracked separately.

The prior `.6` [offline state repair and audit](context6-state-ownership.md) fixes the exact `.5`
retained-purpose regression across repeated selection, repair and focus changes.
It covers all typed goal kinds, new-email/Calendar retries, current-turn cancellation,
explicit corrections/clears, lease-checked persistence and draft/save-receipt retention.
That `.6` repair left prompt wording and tool schemas unchanged; earlier snapshots
remain immutable.

The latest [.6 live resumption](context6-resumption-results.md) completed Alex and
Casey drafts independently and passed Calendar preview, 3 pm revision and the
user-facing closing. It **stopped on a new failure**: “Where do I confirm it?”
selected email review in a Calendar-only chat and returned Gmail draft guidance.
The event state remained intact. Overall conversational acceptance still fails.

At the `.6` stopping point, the third ledger was **10/12 attempts, USD 0.34752047 including GST**:
seven new calls added to the preserved original three. **Two Calendar calls remain
unused** at that point. The final separately authorized `.7` segment above has
now consumed them; there is no remaining model allowance. The other two ledgers are unchanged. No publication,
deployment or provider write occurred. The [.5 failure](context5-results.md) and
[.6 offline repair](context6-state-ownership.md) remain historical evidence. The
live Alex repair repeated purpose, so omitted-purpose retention is specifically
proved by the recorded-response offline replay.

Backend base: released voice repair `bc12ef106659f5ac8b5b79890e0887f1431e29ea`.
The mechanical receipt pins the final `.6` code/test commit and source hashes.
Final `.6` gates: **2,322 backend tests passed, no skips**, **185 focused checks
passed**, and Ruff passed. The first full run's four draft-preservation failures
and their fixes are retained in the evidence; the final run has none.
Frontend runtime/test head: `f4601ff85461283ffe535a847450a0f1427ffd58`, with evidence
head `86af12f5e26aca9cb8a9a8c2ba76e1d2cfdd60bf`. It includes released voice repair,
PR122 `60a7aa8dd02dd94105b90ae1ee68f742cf23d204` and PR126
`1221a2e3bd1b3fe4316ba3f8c4c9e58811243ea1`, plus merged PR127
`e2200f134ddb4a2b772913019291d4afea477874`. PR125 and PR128 remain unmerged/excluded.

Historical `.5` checks: **2,300 backend tests passed, no skips**, **254 frontend unit tests
passed**, TypeScript, Ruff and both builds. PR127 verification passed **12 focused
local browser tests plus one public-origin test**. The earlier PR126 head passed
the full **50 local + one public-origin** suite; that was not repeated in full for PR127. The exact recorded new-Casey call now returns a
normal missing-purpose clarification in one scripted call, with Alex unchanged.
These are historical deterministic/runtime checks. The `.5` acceptance replay was
RED before this repair and now passes offline. Final `.6` backend gates and the
intermediate failures are recorded in the mechanical receipt; no frontend runtime
changed during this repair, so its existing checks were not repeated.

The [design review](../../chat-context-design-review.md) describes implemented
behavior, remaining semantic limits and the coordinated migration requirement.
The [first-stage proposal](first-stage-proposal.md) pins the deployed model,
three diagnostic scenarios, hard caps and official pricing. The user approved the
exact scope on 2026-10-07 at 10:58:05 UTC. CountTokens was repaired using the same
model's bare ID. **All 18 attempts are consumed: 17 completed, one throttled; none
remain.** Recall, goal separation and citations had partial successes, but **0/3
scenarios completed end to end**. See [evaluation status](approved-evaluation-status.md)
and [machine-readable review](live-model-review.json) for the failures, receipt IDs
and USD 0.61530194 conservative cost estimate including 10% GST and failed-call reserve.

The separately approved [second diagnostic](second-stage-results.md) evaluated `.3`
in **9 calls**, about **USD 0.30159 including GST**, leaving nine unused. Draft text
generation improved, but returning to Casey overwrote Alex's current goal. Calendar
previews/revision worked with wrong closing-button guidance; saved-draft restoration
returned status instructions instead of the artifact. The [.4 corrections](structural-fixes-review.md)
add explicit goal binding, current-artifact restoration and action-state guidance.
Those bad calls pass scripted regressions; the later `.4` resumption found the
new-operation repair loop described above. Live semantic acceptance remains a gate.

Previous full backend suite at `398c4ac`: **2,284 passed**, no skips, one Starlette
warning, using real isolated PostgreSQL and scripted model/fake Google adapters.
Earlier full runs at `744982f` (2,264) and `1bcb9c4` (2,283) remain recorded. This includes the
combined approval-isolation regression: chat Always can approve its own resumed
event without approving the separate email-button candidate or changing retained
chat goals. Earlier full and focused results remain in the mechanical receipt.

Previous frontend after PR122: **250 unit tests passed**, **81 focused context/source/controller
tests passed**, and typecheck passed. A new integration test verifies automatic
following before chat, stable source after chat, newest visible incoming reply
target and New chat behavior alongside server-owned focus. Reconciled local/public builds
passed. Full browser suite: **47 passed / 1 skipped**; the skipped public-origin
case then passed against its required synthetic-origin build. The extended meeting-email browser
regression also passed with the new context protocol and a follow-up chat turn.
These are mechanical results, not real Google/Bedrock quality evidence.

The [receipt](mechanical-verification.json) records exact code/test heads, source
hashes and run boundaries. Previous failures and fixes remain disclosed. Prompt `.3`
improved new-goal repair and draft-field completion; `.4` adds the structural
goal/restoration corrections and their versioned tool contract. Four regressions
failed before the correction and passed after it, with 194 broader backend checks,
17 final invariant checks and 53 cancellation/drafting checks. The released voice
files remain unchanged. See [combined review](integration-review.md) for the source,
goal and approval boundaries and remaining architectural/release limits.

Reproduce from `backend/` using the owned disposable database:

```sh
THREADLY_TEST_DB=postgresql+asyncpg://threadly@127.0.0.1:55439/threadly_meeting_email_test \
THREADLY_REQUIRE_TEST_DB=1 python -m pytest -q tests
python -m pytest -q tests/test_chat_context_evaluation_budget.py tools/evaluate_chat_context.py
python -m ruff check app tests tools
```

The first live evaluation exhausted its 18-attempt ledger. After the second used
nine calls, its harness safety window ended during offline fixes. The user then
explicitly approved resuming its remaining nine calls in a new window on 2026-10-07
at 13:24:28 UTC. This [resumption](second-resumption-results.md) consumed them:
**18/18 cumulative second-budget calls, zero remaining, USD 0.61355470 including GST**.
The original nine receipts and first ledger remain intact. This was no third budget;
the separate [proposal](structural-followup-proposal.md) was superseded and never executed.
No production migration, cleanup job, release push, provider write or deployment ran.

Original button commits `53a0d11` (backend) and `fb6d8d5` (frontend) remain preserved
on their independent branches. They are integrated into these context stacks as
`2768f25` and `53e1770`; their tests are included in the combined counts above.
