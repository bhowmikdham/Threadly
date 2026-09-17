# Copy-paste launch prompt for Codex 5.3 Spark

Current integration: read [the MVP workflow map](../mvp-workflow-map.md) and
[progress](../backend-progress.md) first. The user explicitly authorized the combined
MVP PR; the historical one-package prompt below does not override that scope.

Use this prompt in a coding task with this repository available. Selecting Spark
is a user/app setting; these instructions do not change Threadly's Bedrock model.
Merge this handoff documentation into the integration branch before starting a
new implementation branch. Start with B00 unless an evidence-backed checkpoint
says it is complete. Do not
paste the entire roadmap into each request; open the selected task's files.

```text
Continue implementing Threadly's backend using docs/backend-execution/README.md.

First read AGENTS.md, docs/backend-execution/BASELINE.md,
docs/backend-execution/QUALITY-GATES.md and the selected task card.
For the first run, implement B00. On later runs, inspect the last committed
checkpoint and choose the first incomplete task in docs/backend-execution/tasks.json
whose implementation prerequisites are satisfied. Never infer completion from prose
or the presence of a stub. Do not implement the whole roadmap in one PR.

Inspect git status, the remote integration branch and the actual source first.
Known baseline: PR #9 merged into codex/assistant-intent-routing at 4474322;
current baseline migration e9b7120c4a63. These may have advanced. Preserve user
changes and use a codex/ feature branch based on the verified merged baseline.
Read contracts before changing them. Proposed files/routes are labelled as proposed.
Use the current revision-specific draft_envelope, not task.draft_input, for edited drafts.

Before editing, state the task ID, concrete behavior, likely files, acceptance tests
and external dependencies. Then implement the bounded slice completely, including
its meaningful failure cases, migration, runtime docs and fixtures. Write explicit
code consistent with existing FastAPI/SQLAlchemy/httpx conventions. Avoid unrelated
refactors, generic frameworks, guessed providers or unsupported compatibility hacks.

Treat source mail and model output as untrusted data. The backend owns identity,
references, recipients, dates/availability, state and approval. A DraftReview is not
an approval to send. A model may propose an action but cannot approve or execute it.
Never run external writes under the generation worker's automatic retry policy.
Unknown external outcomes require reconciliation before any retry.

Run focused tests while developing, then appropriate full regression checks once
changes settle. PostgreSQL tests must actually run against an isolated disposable
database using THREADLY_TEST_DB and THREADLY_REQUIRE_TEST_DB=1. Never point tests at
a live mailbox DB. Check migrations from empty and existing data, schema drift and
safe rollback. Do not weaken tests, skip DB coverage, or remove validation to get green.
If the task changes prompts, routing or flows, add replay fixtures and pin the new
release without reinterpreting previously queued tasks. Report live evals separately.

Before finishing, review the diff for ownership, races, rollback, privacy, API
compatibility and user-visible outcome correctness. Fix findings and rerun affected
checks. Complete the checkpoint template in docs/backend-execution/CHECKPOINT.md
at docs/backend-execution/checkpoints/Bxx.md. Include actual command results,
remaining blockers, exact commit/branch and the next eligible task. Never put tokens
or private mail in checkpoints. Update original T-task evidence without declaring
broad packages verified prematurely.

Commit the implementation and open a reviewable PR when repository publishing is
authorized. Do not auto-merge or start the next package before its required review
or integration gate. Do not deploy, send real mail, create real invites, install
cloud resources or spend on live model evaluation without that scope being explicitly
authorized. Keep making progress on tests, adapters and docs when credentials are absent.
Missing credentials are not a reason to claim a live integration passed.

Show a milestone progress bar for this slice (not overall product completion).
End with behavior delivered, PR/commit, tests and skips, migration/configuration,
known limitations, and the next task. Do not merely describe code you could write.
```

## Resuming after a merge

```text
The previous PR is merged. Follow docs/backend-execution/START-HERE.md.
Verify the merge, read the last checkpoint, and implement the next eligible B task.
Preserve prior compatibility and run the same quality gates. Show slice progress.
```

## Review prompt for a separate review pass

Use this after implementation. A human or stronger model can run the pass when
available; it is especially useful for B01–B06, B08/B10, B13/B15 and B17/B19.
It is a review recommendation, not an assumption that any model guarantees correctness.

```text
Review the selected Threadly PR against its B-task card, actual diff and
QUALITY-GATES.md. Do not rely on the implementer's summary. Trace authentication,
owner-scoped reads, exact payload/hash binding, lock ordering, transaction/network
boundaries, retry/reconciliation, stale context and migration behavior. Compare API
examples to runtime schemas. Check that tests exercise independent failure cases
rather than copying the implementation. Inspect old queued-release compatibility.
Report concrete regressions with code locations, impact, and a reproducing case;
separate observed failures from hypotheses. Verify any fixes and record remaining
risks. Passing CI alone does not prove live provider or model quality.
```

## When progress needs a decision

Proceed on reversible implementation choices inside the task. Ask only for a
missing product decision or external action authorization that actually blocks the
next dependent step. Record an explicit recommended default and the precise impact.
Do not use “needs review” to abandon implementation before producing a concrete PR.
If context runs short, save a checkpoint describing tested code, uncommitted work,
open failures and exact next command. Do not reset, rebase or discard work to simplify
resuming. Never create a completion record for unimplemented acceptance criteria.
