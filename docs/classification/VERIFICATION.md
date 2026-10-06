# Classification service verification

**Follow-up:** the [visual Flow provisioning record](VISUAL-FLOWS.md) supersedes
this initial Converse-only snapshot for cloud provisioning and model evaluation.
Claude Haiku 4.5 is now selected and the Nova Micro Flow is retired; current
selection/cleanup checks are recorded there.
The [7 October deployment record](ROLLOUT-2026-10-07.md) now records the enabled
backend, latest CI, application-role evaluation and authorized live Gmail checks.
Everything below is the historical initial implementation snapshot, retained to
distinguish the implementation stages.

Recorded 7 October 2026. Scope: the user-requested email classification badge
service on `codex/llm-classification-service`, based on `release/backend` commit
`5298835`. This record does not mark the broader implementation backlog complete.

Status: implementation and local integration checks passed; model evaluation,
live Gmail/Bedrock integration and deployment remain pending. Changes are local
and have not been pushed or deployed.

## Implemented behavior

`POST /threads/{thread_id}/classification` uses the authenticated account's live
Gmail thread, invokes a separately configured Bedrock Converse model, validates
its decision, independently refetches Gmail and rechecks session/account validity
before returning transient badges. It rejects changed sources and expired results.
No classification table, source-body persistence, background import or migration
was added. The model cannot execute tools or external writes.

Outputs are binary `needs_reply`, three priorities, seven BERT-compatible
categories (including IT), and seven BERT-compatible actions. Review/skipped
states have null labels; failures never synthesize No Reply/Low/Other badges.
The separate four-label BERT intent classifier is recorded but not converted into
an email badge. Binary reply is an additional independent prediction.

The [handoff](README.md) contains the endpoint, errors, display mappings,
freshness rules, configuration and frontend fixtures. Runtime API, architecture
and data-model documentation were updated together.

## Checks and results

Tests used an isolated Python 3.13 environment with the release's declared
dependencies, PostgreSQL 16.15 on a disposable local database, fake Gmail HTTP
responses and fake model responses. No real mailbox, production database or AWS
model was used.

| Check | Result |
|---|---|
| Complete backend suite | **1,929 passed, 0 failed, 0 skipped**, 212.54 seconds; one Starlette/httpx deprecation warning. |
| Focused service/provider tests | 55 passed, including strict output validation, evidence membership, context limits, release/source changes, provider failures and cancellation retaining its concurrency slot. |
| Classification API integration tests | 12 passed with real PostgreSQL: authentication/ownership, logout/disconnect/reconnect, concurrent source changes, no DB transaction during external IO, and no mail/snapshot persistence. |
| Affected regression selection | 112 passed before the complete run. |
| Versioned asset/contract tests | Included in the complete run: exported schemas, frontend fixtures, BERT label sets, prompt manifest and replay scoring. |
| Evaluation fixture command | 12 synthetic cases validated; `model_quality_verified: false`. |
| Lint for changed Python files | Passed. |
| `git diff --check` | Passed. |
| Complete backend lint | Six pre-existing UP038 findings under Ruff 0.12.0, in unchanged files listed below. |

Complete suite command, from `backend/` (use an isolated, disposable database;
tests reset its schema):

```bash
THREADLY_TEST_DB=postgresql+asyncpg://threadly@127.0.0.1:55439/threadly_classification_utf8 \
THREADLY_REQUIRE_TEST_DB=1 PYTHONDONTWRITEBYTECODE=1 \
/private/tmp/threadly-classification-venv/bin/python -m pytest -q -p no:cacheprovider
```

The required-database flag prevents a successful result that silently skips the
PostgreSQL integration tests. The temporary database server was stopped after
verification; the command above requires starting a fresh test database.

Existing whole-backend lint findings: `app/pii/masking.py` lines 70, 76, 106 and
140; `app/schemas/calendar_event.py` line 49; `app/workflows/evaluate_summary.py`
line 132. These are outside this change. No unrelated code was rewritten to
silence them.

## Model evidence and remaining dependencies

- Category, priority, action and intent label mappings were compared against the
  saved BERT `config.json`, metadata and corresponding model ZIPs. The
  [snapshot](../../ml/evals/classification/bert-labels.json) records hashes and
  native ID mappings. This establishes label compatibility, not equal predictions.
- The prompt, schema and policy are versioned in
  [the release manifest](../../ml/evals/classification/release.json). The runtime
  release digest also binds the configured model, region, timeout and result TTL.
- [Twelve synthetic evaluation cases](../../ml/evals/classification/v1.json)
  cover every category, priority and action, independent reply decisions,
  missing attachments and hostile email instructions. The evaluation command can
  replay saved predictions or explicitly invoke Bedrock after configuration.
  Fixture validation and fake predictions are not evidence of model accuracy.
- The user has deferred model selection. `CLASSIFICATION_ENABLED=false` and the
  dedicated model ID remains empty. Live invocation, account/region/model access,
  latency, cost and classification quality have not been validated. No model was
  selected from a pre-existing summary configuration.
- Set acceptance thresholds and evaluate a representative held-out dataset after
  selecting the model. Verify the authenticated flow against a designated live
  test mailbox before enabling badges in deployment.
- The implemented workflow uses direct Converse. No visual Bedrock Flow was
  provisioned; a pinned Flow adapter is a later release change that can retain
  this frontend contract.
- Concurrency is bounded per API process, not across replicas or per user.
  No shared cache/deduplication or exactly-once billing guarantee is implemented.
  A second Gmail read reduces stale results but cannot make Gmail/browser state
  atomic; clients must obey the documented invalidation and expiry rules.

## Interruption recovery

The pending complete-test command finished successfully. A process check found
no remaining pytest or dependency-install command; only this task's disposable
PostgreSQL server remained. The original `main` checkout's earlier design files
were preserved; implementation lives in the attached release-based worktree.
