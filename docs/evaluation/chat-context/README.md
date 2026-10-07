# Shared chat-context verification

Current prompt: **`contextual-conversation-1.8.9+chat-context.2`**. The `.1` snapshot
is historical. This isolated implementation is uncommitted and unpublished.
Backend base: `bc12ef106659f5ac8b5b79890e0887f1431e29ea`; frontend base:
`afa2180fa16b4060587002b6be440ce32573c603`. Both include the released voice repair.

The [design review](../../chat-context-design-review.md) describes implemented
behavior, remaining semantic limits and the coordinated migration requirement.
The [first-stage proposal](first-stage-proposal.md) pins the deployed model,
three diagnostic scenarios, hard caps, official pricing and missing authorization.
**No real-model evaluation has run.**

Latest full backend suite: **2,236 passed**, no skips, one Starlette deprecation
warning, using real isolated PostgreSQL and scripted model/fake Google adapters.
This includes independent goals, citations, historical clocks, encrypted worker
context, compaction, retention, source identity, migrations and released voice tests.
Afterward, only the opt-in evaluation harness and its offline guards changed:
**12 guard/rehearsal tests passed; the paid test skipped intentionally**.

Frontend: **235 unit tests passed**, typecheck and local/public builds passed.
The full local browser suite had **46 passed / 1 skipped**; the skipped packaged
public-origin case then passed separately against a public-origin build. These
are mechanical tests, not real Google/Bedrock compatibility evidence.

The machine-readable [receipt](mechanical-verification.json) records exact bases,
source hashes and run boundaries. It preserves previous failures and their fixes.
These are working-tree receipts, not exact committed-head CI or release gates.
The untouched released voice files are verified against their respective bases.

Reproduce from `backend/` using the owned disposable database:

```sh
THREADLY_TEST_DB=postgresql+asyncpg://threadly@127.0.0.1:55439/threadly_calendar_field_test \
THREADLY_REQUIRE_TEST_DB=1 python -m pytest -q tests
python -m pytest -q tests/test_chat_context_evaluation_budget.py tools/evaluate_chat_context.py
python -m ruff check app tests tools
```

Do not set the live-evaluation opt-in without the explicit approval described in
its proposal. No production migration, cleanup job, release push, provider write
or deployment was performed.

The meeting-email button is separately implemented on backend commit
`53a0d11c13cf4806d12cb5380487df5c0e3891d5` and frontend commit
`fb6d8d5b765ff1bcd5eaee6b2e00ae84d1c28fd8`. Its tests and contract are in each
branch's `docs/meeting-email-create-event.md`; it is not merged into this tree or
included in the core full-suite count. Joint integration still needs verification.
