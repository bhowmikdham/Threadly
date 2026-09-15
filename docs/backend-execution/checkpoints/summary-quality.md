# Summary quality correction checkpoint

- User-directed correction after merged PR #15; mapped to T07/T18 and B16/B17 evaluation work.
- Branch `codex/summary-quality`; verified base `c190126`.
- Status: implemented, locally verified, ready for PR review. No deployment or live model evaluation.

The reported console response matches the old prototype envelope. Its prompt asks
for separate decisions/requests/questions/coverage without brevity constraints;
the supplied input shape differs from its documented source contract. The screenshot
also shows unsupported assumptions, an invented evidence label, JSON fences and
repeated questions. The three nodes completed; no EC2 capacity fault was shown.

Added shared versioned concise summary policy, deterministic budget/shape/repetition
checks and a release wrapper preserving historical prompts/validators. New native
and runtime Flow summary paths use the same policy. Public artifact schema, source
mapping, draft envelopes, replay hashes and task fencing are unchanged. UI source
binding remains before generation. Existing old prototypes are not modified.

A separate CloudShell launcher reuses the established restricted role/template/
prepare/rerun helpers to create just one new console summary experiment. Prompt,
graph and model configuration drive its content-addressed name; it cannot overwrite
an existing mismatched stack. It includes a messages/user_request input adapter and
prints a new test URL. It does not enable the backend or invoke the model. The
fixed-prompt console graph cannot be admitted to the runtime registry contract.

Evidence:

- Focused backend suite: **139 passed, zero skipped**.
- Full backend suite, Python 3.12 + required disposable PostgreSQL 16: **345 passed,
  zero skipped**, including 27 new summary tests and all prior runtime/migration tests.
- CloudShell provisioning suite: **19 passed** (four new summary experiment cases).
- Ruff backend/new infra, both plan/handoff validators and whitespace checks: pass.
- Render-only summary template and cfn-lint: pass, zero AWS calls.
- CI includes the new console launcher tests/template validation; actual hosted result
  is recorded by the PR check suite.

The six versioned synthetic source/reference/rubric cases replace private names,
organizations and identifiers. They cover forwarded invoice attribution, a later
payment confirmation, FYI without invented action, a material question, quoted
instruction injection and conflicting amounts. Human-authored expected outputs and
fake SDK results do not establish live Haiku quality. The explicit billed evaluator
records observed output, contract hashes and review rubrics; semantic review remains
pending even when all contract checks pass. No live quality percentage is claimed.

Review: new tasks alone receive the quality release; a regression test explicitly
queues a historical task and verifies the old verbose contract is still accepted.
Changed quality policy fails saved tasks closed. The shared output builder retains
backend-only coverage/evidence; model assumptions/IDs are rejected as extra fields.
No source text, raw errors or PII is added to application logs. Raw observed output
is saved only in the explicit synthetic evaluation report for human review.

Rollout: no new migration/dependency, existing Alembic head `e9b7120c4a63`. Upgrade API
and worker together, then evaluate the selected runtime Flow before enabling it.
Stop new admissions and drain/cancel quality-release tasks before an older-worker
rollback. Creating the console experiment does not change the EC2 application or
any old console Flow. Frontend should render overview/action fields, with source
coverage separate from user-facing summary text.

Next required evidence: run the new console Flow on the synthetic cases and the
reported example privately, review factual attribution/relevance/duplication, and
retain results with the new Flow/prompt release. Then validate through the deployed
backend and frontend. B08 clarification work is still pending after this correction;
no broader workflow package is marked complete by this summary fix.
