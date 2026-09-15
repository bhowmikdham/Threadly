# Summary grounding and master-workflow handoff

User-directed continuation after PR #17. Branch `codex/summary-grounding-contract`,
based on verified merged integration commit `760b0f5`.

## Delivered

- Summary policy candidate `summary-quality-1.0.2`: distinguish descriptive updates,
  explicitly requested/committed outstanding work, offers, decisions and unanswered
  source questions. No inferred advice in overview, no minimum word target.
- Preserve 1.0.0 and 1.0.1 policies and their historical contract hashes. Real queued
  task tests verify each original prompt still runs. Public output schema unchanged.
- Ten synthetic reference/rubric cases with explicit regression constraints.
  Shared offline/live replay separates schema validity from case-specific failures.
  Reports retain unknown deployment identity, missing cases and pending human review.
- Record the two user-pasted 1.0.1 results. Delivery passes schema but fails regression
  checks; room passes those checks. Eight cases in the expanded suite remain not run.
  This is not a fresh candidate evaluation or a measured model success percentage.
- `docs/master-workflow.md` maps the actual backend coordinator, classifier boundary,
  current installed operations and proposed compound dependency graph. B10 now
  explicitly includes summary + scheduling + reply, negation, separate outputs and
  dependent-step recovery. B10 remains planned; no compound executor is claimed.
- A real-worker test verifies an explicit triple-operation model proposal cannot
  execute only the supported summary. It currently fails as `invalid_route_output`;
  this does not prove detection of every misclassification that omits a user clause.

## Verification

- Full backend suite before the final offline-mode guard: **373 passed, zero skipped**, required disposable
  PostgreSQL 16, Python 3.12. Tests include migrations, ownership, leases, old releases,
  generation, Flow adapters and the added compound-proposal rejection.
- Final review found that a JSON `null` observations file could select the wrong
  evaluator branch. Selection now depends only on the explicit CLI mode; a regression
  test rejects null without invoking the live evaluator. **23 grounding tests passed**
  after this change; hosted CI runs the full updated suite.
- Focused final summary/routing run before full regression: **82 passed**, zero skipped.
- Infrastructure suite: **19 passed**, no AWS/model calls.
- Configured Ruff checks (`backend/app`, `backend/tests`, `infra/bedrock`): pass.
- Both planning validators and generated B cards check: pass.
- Summary candidate render-only template: pass; cfn-lint 1.56.3: pass.
  Synthetic stack fingerprint prefix `0541b4f599b9` is not a live AWS resource ID.
- Offline CLI replay of saved 1.0.1 observations: expected exit 1, 2 contract passes,
  1 regression pass, 1 regression failure and 8 not run. Unknown/malformed fixtures
  and contradictory assertions are rejected before any billed evaluation calls.
- Historical 1.0.0/1.0.1 hashes independently read from the prior worktree and pinned
  in tests. Summary policy compatibility is not inferred from names alone.

The first sandboxed test attempt could not reach local PostgreSQL; final checks
ran with local-network access against the disposable container. The configured lint
scope is app/tests, not the historical generated Alembic files. No unrelated source
or another agent's classifier worktree was modified.

## Not verified / rollout

Candidate 1.0.2 has no live model results. Fixture assertions are not universal
semantic entailment validation; paraphrased hallucinations can pass structural
checks. Review every clause against its sources in a complete candidate replay.
No EC2 deployment, AWS resource mutation, model invocation or Google write occurred.
No schema migration or new package dependency is needed for this change.

Promote only after the complete ten-case gate in `docs/summary-quality.md`, then
verify the published runtime Flow through the deployed backend and frontend.
Deploy matching API/worker releases; retain older policies/resources for queued work.
The six prepared prototype Flows remain distinct from the production registry.

Next implementation: B08 durable clarification and the bounded B10 planner/executor
according to its dependencies. Classifier integration needs actual command-domain
labels/evaluation. Calendar handlers B12/B13 and complete compound tests must be
available before enabling the scheduling triple. Exact approval and external writes
remain separate B02–B06/B14–B15 work; draft generation never authorizes sending.
