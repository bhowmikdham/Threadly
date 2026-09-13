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
