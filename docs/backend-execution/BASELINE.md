# Baseline and source map

Observed 14 September 2026 at merged PR #9 (`4474322`). Read the actual checkout
before editing: subsequent work may move these symbols or finish tasks below.

## Existing runtime surfaces

| Existing path | Read for | Preserve |
|---|---|---|
| `backend/app/api/routes/assistant.py` | `task_view`, artifact reads, revision/history/review endpoints, event replay | JWT ownership; latest artifact selection; finite SSE contract |
| `backend/app/assistant/tasks.py` | `submit`, `owned_task`, `claim_next`, `finish`, `save_route`, `add_event` | Request hash compatibility; task-first locking; generation lease fencing |
| `backend/app/assistant/worker.py` | `run_once`, release matching, routing and generation outside transactions | Only generation/read retries; prior queued releases retain behavior |
| `backend/app/assistant/routing.py` | Current contextual manifest, capability binding, installed operation sequences | Whole compound request validation; no silent supported subset |
| `backend/app/assistant/routing_v1.py` | Frozen contextual-task-1.0.0 behavior | Do not modify historical prompts while adding new routing |
| `backend/app/planner/intent_router.py` | Strict proposal parsing and exact-command fast paths | No model-selected IDs/tools/write authorization |
| `backend/app/planner/intent_prompt.py` | Router prompt/version | Change prompt and fixtures/version together |
| `backend/app/assistant/context.py` | `capture_thread`, bounded immutable source excerpts | Ownership, source versions and explicit coverage |
| `backend/app/assistant/summary.py` | `digest`, summary schema/prompt, evidence binding | Source IDs mapped by backend, partial coverage |
| `backend/app/assistant/drafting.py` | `bind_input`, model text validation and initial draft artifacts | Explicit To/Cc/Bcc; fixed reply subject and selected message |
| `backend/app/assistant/draft_review.py` | Revision edits, `payload_hash`, local blockers and review state | Review is not Send approval; immutable revision envelope |
| `backend/app/schemas/assistant.py` | Strict requests, routes, literal mailbox validation | Extra-field rejection; current client compatibility |
| `backend/app/schemas/draft_review.py` | Full-edit and review input | Subject/header controls, full replacement semantics |
| `backend/app/db/models.py` | All persistent models and ownership constraints | Alembic parity, cascade/unknown-write implications |
| `backend/app/auth/google.py` | `GoogleTokens`, token exchange/refresh and scope constant | Scope requests are not proof of actual grants |
| `backend/app/auth/service.py` | `exchange_code`, `get_valid_access_token` | Existing token encryption and refresh preservation |
| `backend/app/auth/crypto.py` | Token encryption | No plaintext credentials in rows/events/prompts |
| `backend/app/api/routes/auth.py` | Existing OAuth/JWT boundary | Validate actual integration flow before adding state/PKCE contracts |
| `backend/app/sync/gmail.py` | `GmailClient`, paginated HTTP reads | Mockable httpx transport, sanitized errors |
| `backend/app/sync/worker.py` | Backfill/history replay and sync fencing | Do not reset cursors or race existing commits |
| `backend/app/db/repositories.py` | Message ordering, owned mailbox queries, cache fencing | Owner filter and source fidelity for all new retrieval |
| `backend/app/model_client/bedrock.py` | Lazy Bedrock Runtime adapter | Explicit provider choice, bounded calls, error sanitization |
| `backend/app/model_client/client.py` | Provider selection and cloud masking | No silent new provider/region fallback |
| `backend/app/pii/masking.py` | Current cloud egress masking | New Flow/bridge paths must preserve policy |
| `backend/app/config.py`, `.env.example` | Actual supported configuration | Proposed variable names are not implemented settings |
| `backend/tests/conftest.py` | Test DB guard, fake provider isolation and fixture cleanup | Tests truncate/drop data; isolated DB only |
| `backend/tests/test_assistant_migration.py` | Empty install, baseline preservation, downgrade guards | Extend head and representative fixtures for each migration |
| `backend/tests/test_durable_tasks.py` | Task concurrency and lease tests | Regression coverage for all new workers |
| `backend/tests/test_contextual_routing.py` | Durable route checkpoint and prior-release tests | Bound operations and no partial compound execution |
| `backend/tests/test_draft_workflows.py` | Envelope/model boundaries | Initial generation and request replay compatibility |
| `backend/tests/test_draft_review.py` | Edit/review concurrency, stale sources and owner checks | Original 27 new cases are not optional |
| `.github/workflows/backend-ci.yml` | Python/PostgreSQL CI | DB required; current path filters do not run on docs-only PRs |
| `docker-compose.yml`, `Makefile` | Existing API/worker startup and commands | No production `down -v` or blind migration commands |
| `infra/deploy/README.md` | Deployment starting point | Contains historical legacy inference setup; update in B19/B20 |

## Current constraints that affect upcoming code

1. `AssistantJob` is one row per task. The generation worker retries reads/models
   with bounded attempts. It is **not** an external-action outbox.
2. `ArtifactRevision` is unique on `(task_id, revision)`; one stream per task.
   Summary generation is revision 1 and draft edits append revisions. Multi-step
   artifacts need an explicit migration/result pointer in B10; do not reuse the
   draft counter for unrelated kinds and then select whichever revision is largest.
3. `task.draft_input` is original input. Read `artifact.draft_envelope` for edits.
   Positional recipient refs are local to that exact envelope.
4. A `DraftReview` is an acknowledgement. It has no executable action, expiry,
   provider attempt or Send authorization. Build separate action approval records.
5. Tasks in `needs_clarification` have closed jobs; continuation currently returns
   501. A raw answer cannot simply be appended and a job reopened without version,
   question, checkpoint and idempotency logic.
6. Context capture holds at most 50 messages / 12,000 body characters and is partial.
   Backend chronology is not the extension's visual ordering. Do not answer “third”
   from chronological order without the relevant captured UI map.
7. Current draft reply binding includes one original RFC Message-ID, not a completed
   outgoing References chain/MIME builder. `Re:` generation behavior needs Gmail
   test-account verification; don't assume its shape proves threading correctness.
8. Requested Gmail scopes are constants; actual granted scopes are not persisted.
   The code commits inside token refresh and exchange services. Never call that
   helper inside a transaction that must atomically approve/enqueue an action.
9. OAuth error text currently originates from provider response bodies. B01 must
   sanitize errors and review actual state/redirect/identity verification before
   enabling new capabilities. This is a concrete code-review requirement, not a
   claim that an exploit or production incident has been demonstrated.
10. Existing thread/account cascades remove assistant state. External in-flight
    writes cannot simply lose their only reconciliation record; B02/B19 must define
    coordinated deletion and minimal permitted audit retention.
11. `backend/app/api/routes/draft.py`, entities/commitments/voice routes and extractor/
    RAG modules contain stubs. Folder existence does not mean capability exists.
12. `frontend/` here contains a README. Locate and inspect the actual frontend
    integration branch/checkouts in B00/B18; don't fabricate UI integration evidence.
13. No Bedrock Flow invocation, Calendar client, external action executor or active
    proactive trigger exists at this baseline. The existing source-backed summary,
    reply and compose should remain functional while those are introduced.
14. Migration head is `e9b7120c4a63`. Never invent the next revision's parent from
    memory; inspect the current Alembic graph after every merged dependency.

## Required baseline commands

```bash
git status --short --branch
git log -5 --oneline
rg --files -g AGENTS.md
rg -n 'revision =|down_revision =' backend/alembic/versions
python3 docs/backend-execution/tools/validate_handoff.py
python3 docs/implementation-playbook/tools/validate_plan.py
```

Git fetch/PR inspection may require the environment's network permission. Verify
branch provenance read-only before switching. Normal commit/push follows session
authorization; never change the user's saved GitHub account persistently just to
publish. Avoid exposing credentials in logs. See QUALITY-GATES for disposable DB
commands and the distinction between code checks and live validation.
