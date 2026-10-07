# Shared chat-context verification

Current prompt: **`contextual-conversation-1.8.9+chat-context.2`**. The `.1` snapshot
is historical. The integrated implementation is committed locally and unpublished.
Backend base: `bc12ef106659f5ac8b5b79890e0887f1431e29ea`; frontend base:
`afa2180fa16b4060587002b6be440ce32573c603`. Both include the released voice repair.

The [design review](../../chat-context-design-review.md) describes implemented
behavior, remaining semantic limits and the coordinated migration requirement.
The [first-stage proposal](first-stage-proposal.md) pins the deployed model,
three diagnostic scenarios, hard caps and official pricing. The user approved the
exact scope on 2026-10-07 at 10:58:05 UTC. AWS subsequently rejected CountTokens
with `ValidationException`; see [evaluation status](approved-evaluation-status.md).
**No paid inference or semantic model evaluation has run.**

Combined full backend suite: **2,260 passed**, no skips, one Starlette deprecation
warning, using real isolated PostgreSQL and scripted model/fake Google adapters.
The additional combined approval-isolation test passed separately after full-suite
collection. It proves chat Always can approve its own resumed event without approving
the separate email-button candidate or changing retained chat goals. Runtime code
was unchanged between these runs. Earlier evaluation-guard/rehearsal checks had
12 passes and the intentionally skipped live test; those offline cases are now also
included in the full combined suite.

Combined frontend: **240 unit tests passed**, typecheck and local/public builds
passed. Full browser suite: **47 passed / 1 skipped**; the skipped public-origin
case then passed against its required build. The extended meeting-email browser
regression also passed with the new context protocol and a follow-up chat turn.
These are mechanical results, not real Google/Bedrock quality evidence.

The [receipt](mechanical-verification.json) records exact code/test heads, source
hashes and run boundaries. Previous failures and fixes remain disclosed. Final
commits after the tested code/test heads update evidence only. The released voice
files remain unchanged. See [combined review](integration-review.md) for the source,
goal and approval boundaries and remaining architectural/release limits.

Reproduce from `backend/` using the owned disposable database:

```sh
THREADLY_TEST_DB=postgresql+asyncpg://threadly@127.0.0.1:55439/threadly_calendar_field_test \
THREADLY_REQUIRE_TEST_DB=1 python -m pytest -q tests
python -m pytest -q tests/test_chat_context_evaluation_budget.py tools/evaluate_chat_context.py
python -m ruff check app tests tools
```

The approved live evaluation stopped at its required CountTokens preflight.
Do not use a different model/region, an uncounted fallback or larger caps to continue.
No production migration, cleanup job, release push, provider write or deployment ran.

Original button commits `53a0d11` (backend) and `fb6d8d5` (frontend) remain preserved
on their independent branches. They are integrated into these context stacks as
`2768f25` and `53e1770`; their tests are included in the combined counts above.
