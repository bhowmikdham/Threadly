# MVP release acceptance matrix

Owner: backend + AI jointly; frontend acceptance follows the deferred client work.
A pass in one column does not imply another column passed. Store synthetic case IDs,
commit, migration head, model/profile, prompt hashes, aliases/versions, outcome and
latency; no private messages or credentials in reports.

| Scenario / required outcome | Offline contract evidence | Live/pilot evidence |
|---|---|---|
| Summary: unresolved invoice vs later paid confirmation; no invented tasks | Existing summary quality corpus/tests | Pending current Haiku holdout |
| Informational minutes: concise FYI, empty actions | Existing summary quality corpus | Pending |
| Delayed delivery: uncertainty retained, no invented request to confirm | Existing summary quality corpus | Pending |
| Reply/new draft: exact selected targets, editable revision | Draft/editor/action suites | Pending recipient/MIME verification |
| Plan: supported quote, explicit owner/date, dependencies | MVP workflow + auxiliary replay tests | Pending semantic entailment review |
| Selected plan: unselected items excluded, edits invalidate draft | Plan/grounding tests | Pending user acceptance |
| Three outputs: summary + slots + draft are individually visible | MVP workflow/coordinator tests | Pending actual model + Calendar run |
| Negation, dropped clause, unsupported send/book combinations | Coordinator + prior command tests | Pending paraphrase/adversarial holdout |
| “4” with context vs unresolved AM/PM; DST, timezone, message anchors | Existing scheduling/extraction tests | Pending live comparison |
| Later “second option”: historical mapping, user review, fresh check | Meeting response tests | Pending semantic holdout |
| Other: scoped lookup, partial coverage, no-match not global absence | Read/facts/mail-search suites | Pending product acceptance |
| Gmail timeout: reconcile before any repeat send | Existing Gmail worker/reconciliation suites | Pending controlled account |
| Calendar timeout/crash: exact-ID reads, no second insert | Calendar action suite | Pending controlled calendar |
| Source/account/lease changed, approval stale, cancellation | Existing lifecycle + new workflow/action suites | Pending staging fault exercise |
| Background sync: page restart, history expiry, no partial publication | Background sync + Gmail tests | Pending real backfill sizing |
| Kill switch, per-user status, conservative cleanup | MVP operations tests | Pending operator rehearsal |
| Install/upgrade/guarded downgrade and schema drift | Migration suite with disposable PG | Pending deployed backup restore |

## Live quality procedure (explicitly authorized, billed run)

1. Select a fixed commit and model/profile. Export prompts with
   `python -m app.workflows.mvp_assets --output /tmp/mvp-prompts.json` and save hashes.
   Record both registry manifests, excluding credentials. Existing summary and
   scheduling evaluators remain the starting harnesses for those operations.
2. Use synthetic or consented test-account fixtures only. Cover every row above with
   at least five paraphrases, plus contradictory/negated commands, source injection,
   incomplete source, different timezones and partial provider coverage. Keep a holdout
   disjoint from prompt-development examples; AI team labels expected clauses, facts,
   unresolved fields and allowed actions before inference.
3. Evaluate through the actual configured runtime path. API workers must use the same
   model and registries as the release under test. Record first-attempt failures and
   retries separately; do not manually edit outputs before scoring.
4. Zero tolerance for unauthorized writes, foreign-source leakage, fabricated source
   IDs, substituted times, approval bypass or blind write retry. Any such defect blocks
   rollout. For language quality, report exact numerators/denominators per intent,
   omissions, unsupported additions, unnecessary clarification and reviewer disagreement.
   Agree the quality threshold with the product owner before scoring; do not pick one
   retrospectively to make a small run pass.
5. Controlled real sends/events require explicit tester approval of each exact payload.
   Compare sent MIME/event fields with preview; simulate timeout/restart only on the
   controlled account. Inspect unknown results before any human-created new action.
6. Record performance, provider errors, queue age and estimated usage for the small
   pilot workload. No load/cost capacity claim follows from the local suite.
7. Sign off backend/API, AI quality and later frontend experience separately. Keep
   writes off outside the explicit pilot allowlist until acceptance is recorded.

## Local reproducible gate

From `backend/`, with a disposable PostgreSQL 16 database:

```bash
THREADLY_REQUIRE_TEST_DB=1 THREADLY_TEST_DB='<disposable asyncpg DSN>' python -m pytest -q
python -m ruff check app tests
python -m app.workflows.mvp_assets --check --output fixtures/mvp/prompts-v1.json
python -m unittest discover -s ../infra/deploy/ec2/tests -q
python -m unittest discover -s ../infra/bedrock/tests -q
```

The migration test creates/drops its own temporary database and proves compatibility
with preserved mailbox data. Do not point this suite at EC2 or a real mailbox database.
