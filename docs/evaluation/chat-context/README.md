# Shared chat-context verification

Current prompt: **`contextual-conversation-1.8.9+chat-context.4`**. Earlier snapshots
remain immutable. `.2` and `.3` had bounded live diagnostics; `.4` has only offline
verification. The integrated implementation is committed locally and unpublished.
Backend base: `bc12ef106659f5ac8b5b79890e0887f1431e29ea`; frontend base:
`afa2180fa16b4060587002b6be440ce32573c603`. Both include the released voice repair.
The frontend now also includes upstream PR122 at
`60a7aa8dd02dd94105b90ae1ee68f742cf23d204`; the reconciled integration/test head is
`4e4088b929ebd50184d128d1e63e0c8c07d9b2c3`.

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
The exact bad calls now pass scripted regressions; live semantic acceptance remains
a release gate.

Final full backend suite at `398c4ac`: **2,284 passed**, no skips, one Starlette
warning, using real isolated PostgreSQL and scripted model/fake Google adapters.
Earlier full runs at `744982f` (2,264) and `1bcb9c4` (2,283) remain recorded. This includes the
combined approval-isolation regression: chat Always can approve its own resumed
event without approving the separate email-button candidate or changing retained
chat goals. Earlier full and focused results remain in the mechanical receipt.

Current frontend after PR122: **250 unit tests passed**, **81 focused context/source/controller
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

The first live evaluation exhausted its 18-attempt ledger. The second used 9 of its
separately approved 18 attempts; its deadline expired at 12:22:26.360372 UTC on
2026-10-07. Neither ledger changed during the fixes. The nine unused calls remain
unused; the window must not be reset. A concrete [follow-up proposal](structural-followup-proposal.md)
is ready for separate approval. No third run is authorized or started.
No production migration, cleanup job, release push, provider write or deployment ran.

Original button commits `53a0d11` (backend) and `fb6d8d5` (frontend) remain preserved
on their independent branches. They are integrated into these context stacks as
`2768f25` and `53e1770`; their tests are included in the combined counts above.
