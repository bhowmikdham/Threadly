# Shared chat-context verification

Current prompt: **`contextual-conversation-1.8.9+chat-context.3`**. Earlier snapshots
remain immutable; the live diagnostic evaluated `.2`, not `.3`. The integrated implementation is committed locally and unpublished.
Backend base: `bc12ef106659f5ac8b5b79890e0887f1431e29ea`; frontend base:
`afa2180fa16b4060587002b6be440ce32573c603`. Both include the released voice repair.

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

Final full backend suite at `744982f`: **2,264 passed**, no skips, one Starlette
warning, using real isolated PostgreSQL and scripted model/fake Google adapters.
The focused model-findings/guard run also passed **62 tests**. This includes the
combined approval-isolation regression: chat Always can approve its own resumed
event without approving the separate email-button candidate or changing retained
chat goals. Earlier full and focused results remain in the mechanical receipt.

Combined frontend: **240 unit tests passed**, typecheck and local/public builds
passed. Full browser suite: **47 passed / 1 skipped**; the skipped public-origin
case then passed against its required build. The extended meeting-email browser
regression also passed with the new context protocol and a follow-up chat turn.
These are mechanical results, not real Google/Bedrock quality evidence.

The [receipt](mechanical-verification.json) records exact code/test heads, source
hashes and run boundaries. Previous failures and fixes remain disclosed. Prompt `.3`
adds new-goal repair guidance and explicit draft-field descriptions after the live
run; its fresh mechanical checks do not establish improved real-model behavior. The released voice
files remain unchanged. See [combined review](integration-review.md) for the source,
goal and approval boundaries and remaining architectural/release limits.

Reproduce from `backend/` using the owned disposable database:

```sh
THREADLY_TEST_DB=postgresql+asyncpg://threadly@127.0.0.1:55439/threadly_calendar_field_test \
THREADLY_REQUIRE_TEST_DB=1 python -m pytest -q tests
python -m pytest -q tests/test_chat_context_evaluation_budget.py tools/evaluate_chat_context.py
python -m ruff check app tests tools
```

The approved live evaluation exhausted its 18-attempt ledger. Further real-model
evaluation, including `.3`, requires a new bounded proposal and explicit approval.
Do not reset the existing ledger or treat unused dollars as additional call permission.
No production migration, cleanup job, release push, provider write or deployment ran.

Original button commits `53a0d11` (backend) and `fb6d8d5` (frontend) remain preserved
on their independent branches. They are integrated into these context stacks as
`2768f25` and `53e1770`; their tests are included in the combined counts above.
