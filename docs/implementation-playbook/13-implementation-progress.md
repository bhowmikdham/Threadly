# Implementation progress

## Bedrock adapter — T01/T04 partial

Branch: `codex/bedrock-provider`. Status: implemented, awaiting review and live AWS
validation. No complete backlog task is marked verified.

Added explicit Bedrock/legacy selection, text-only Converse inference, bounded
SDK calls off the event loop, cloud input masking and sanitized failure handling.
Existing summary SSE consumes validated Bedrock text as one buffered chunk.
Long model identifiers fit the existing provenance column using a stable digest.
ADR 003 supersedes the old inference placement decision.

Verification on Python 3.12.14 (13 September 2026): `ruff check app tests` passed;
`python -m pytest -q` passed 34 tests and skipped 7 PostgreSQL tests because no test
database was reachable. Added 18 synthetic adapter/configuration tests, including
failure, truncation, unexpected tool content, cancellation cleanup and model
selection. These are not live model-quality results. Test dependencies were
installed in a disposable virtual environment; Chroma was not needed or installed.

Configuration: see `.env.example` and ADR 003. Bedrock stays opt-in; no real AWS
model, IAM role or Flow has been configured or invoked. No production deployment.

Next: typed intent proposal routing, then durable request/job/context storage and
the summary vertical slice. Existing summary cache remains keyed by message ID;
the task-based replacement must add model/prompt/context-aware invalidation.

## Intent preview — T01/T06 partial

Branch: `codex/assistant-intent-routing`, stacked on `codex/bedrock-provider`.
Status: implemented, awaiting review and live model evaluation. This is a routing
foundation, not completion of T06 or any workflow.

Added strict assistant request/route models, a versioned routing prompt, exact
command fast paths for all five intents, and bounded model fallback. The API is
`POST /assistant/route-preview`; it requires JWT authentication, accepts only the
instruction and optional hint, and always returns `execution_ready: false`.
It does not accept unvalidated mailbox context or attempt task continuation.

Schema validation rejects extra fields, write operations, duplicate JSON keys,
invented reference IDs, invalid parameter types and unsupported operation order.
Backend checks add missing source/reply/recipient/timezone/duration/action-target
preconditions. Extracted intent and numeric parameters still need model quality
evaluation and downstream semantic validation. There is no automatic model retry.
JWT subject validation now returns 401 instead of 500 for malformed identities;
validation errors omit raw request bodies and nonserializable exception context.

The prompt version is `intent-preview-1.0.0`. The full contextual 27-case planning
corpus remains a future integration evaluation: this preview deliberately has no
source snapshots, saved tasks or calendar preferences. Synthetic model fixtures
test validation and control flow, not whether a real model classifies accurately.

Verification: `ruff check app tests` passed; `python -m pytest -q` passed 83 tests,
with the same 7 PostgreSQL tests skipped. The routing slice adds 49 tests covering
all five rule paths, model-based compound proposals, boundary rejection, API
authentication and shared contract examples. Planning artifact validation also
passed (15 Markdown files, 24 work packages, 9 schema examples, 27 seed cases).

Reproduce locally with Python 3.12+ and backend development dependencies:

```bash
cd backend
python -m pytest -q
ruff check app tests
```

CI provides PostgreSQL for the seven integration tests. A local disposable test
database can be selected using `THREADLY_TEST_DB`; the existing test fixtures
drop/truncate tables, so never point this variable at a live mailbox database.

Next implementation branch: T05 durable task/job storage plus T02 source fidelity;
then bind authorized snapshots and integrate the first summary task. Add the
Flow registry/invocation adapter after AWS configuration and live smoke tests.
