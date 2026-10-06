# Calendar context and tool contract repair

Scope: current backend `c6225a4` plus the existing creation/recovery draft #94
(`c0df860`) and precision draft #96 (`7a590d9`), preserving current voice and public
Calendar eligibility. Companion picker #93 is integrated with frontend `71f0343`.
No production activation is part of this change.

Four PostgreSQL regressions failed on the imported baseline: polite creation,
full titles containing Cancel/Update, and an availability detour deleting pending
event state. The implementation uses typed create/resume/revise/cancel intent,
field replace/clear/remove changes, and per-field source provenance. Old text is
not concatenated into new date/time constraints. Source validation and independent
creation authority remain backend checks; a model cannot grant consent or approval.

The draft retains its original expiry and resolved date across reads and reopen.
Corrections retire only undispatched immutable actions under account/task/action
locks with current conversation lease validation. Old approvals/claimed workers
and picker handles are fenced. Resuming unchanged details returns the same action.
In-flight/unknown outcomes cannot trigger an automatic replacement. Filling a
missing field retains a previously selected destination with fresh ACL/version checks.

Validation commands (disposable PostgreSQL on local port 55447, synthetic data):

```sh
THREADLY_REQUIRE_TEST_DB=1 THREADLY_TEST_DB=<isolated-postgresql-dsn> python -m pytest -q
python -m ruff check app tests tools
python -m app.workflows.mvp_assets --check --output fixtures/mvp/prompts-v1.json
python -m unittest discover -s ../infra/deploy/ec2/tests -q
```

Focused and full results are recorded in the PR checks and review evidence. Initial
focused combined language/picker/correction run passed 84 tests; separate throttle
replay passed 13. The Ashu fixture now preserves its original midnight relationship
at a future synthetic date, avoiding expiry against PostgreSQL's real clock. The
new context contract tests additionally cover field/source injection, stale lease,
old unstructured chat clarification, removal/clearing, claimed-worker fencing,
unknown outcome rejection, old approval refusal and exact retry identity.

Conversation release `contextual-conversation-1.8.0` has a new complete prompt/tool
snapshot. Prior release snapshots remain byte-identical. The scripted decisions
are replayable control-flow/schema evidence, not a measured live-model success
rate. No Google write, invitation, email send, paid model/provider call, credential
change, new permission, remote PR merge or deployment was performed. No migration or new
runtime flag is required. Live language/provider acceptance remains a separate,
explicitly authorized rollout gate.
