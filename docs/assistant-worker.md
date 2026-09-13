# Durable summary worker: implementation and operations

This is the first native backend execution path behind the assistant API. It
routes saved free-text requests across five intents and executes summaries of
saved synced-thread excerpts. Bedrock Flows, other intent execution, continuation
and approval/external execution remain later slices. See [routing handoff](assistant-routing.md).

## Local startup

Use Python 3.12+, install the backend dependencies, and configure `DATABASE_URL`
and model settings in the worker and API environments. Both processes must use
the same configuration. Local `.env` lookup is relative to the current directory;
Compose injects the repository-root `.env` explicitly.

```bash
cd backend
python -m alembic upgrade head
python -m app.assistant.worker
```

Run the API in another terminal with the same environment. `--once` handles one
available job and exits; the default worker polls every two seconds when idle.
No worker is started automatically by FastAPI. A submitted task remains queued
until a worker runs, including across API or browser restarts.

For the existing Compose stack, after configuring `.env`:

```bash
docker compose run --rm api python -m alembic upgrade head
docker compose --profile assistant up -d --build assistant-worker
```

The worker uses the API image and its database network. The optional profile
avoids starting a worker before an operator has applied the migration. Deploy
matching API/worker images. Do not use dev `create_all` as a migration substitute.
There is no Chroma dependency in this workflow.

## Client sequence

1. Authenticate and sync Gmail through the existing endpoints.
2. Capture `POST /assistant/context-snapshots` with the active Gmail thread ID.
   Show saved scope and omission/truncation counts to the user.
3. Submit `POST /assistant/requests` with a new request ID, the returned snapshot
   ID, the full user instruction, and `continuation: null`. Context may be null
   when the user needs to select a source; the worker saves a clarification.
4. Keep the returned task ID. Fetch task state or replay saved SSE events. Use
   `GET /assistant/tasks` to recover task history after reopening the application.
5. On success, fetch the artifact and display source references. The saved
   snapshot endpoint provides the exact captured excerpts behind those IDs.
6. To cancel queued/running generation, post its current expected version. On
   409, reload state; never infer cancellation from a disconnected browser.

The preview classifier is separate. Do not take its proposed operations and invoke
tools in the browser. The worker checkpoints validated routes and dispatches the installed summary
workflow; other intents return saved clarification/unavailable outcomes. Render
`needs_clarification` questions from the task view and submit a fully restated
request with a new ID after the user answers. In-place continuation is not yet installed.

## Persistence and recovery

Request acceptance inserts task, job and initial event in one PostgreSQL
transaction. A worker claims using a task row lock with `SKIP LOCKED`, commits a
unique lease token, then runs inference outside the database transaction.
[PostgreSQL documents this locking option for queue-like consumers](https://www.postgresql.org/docs/16/sql-select.html).

The lease lasts 180 seconds and routing/checkpoint/generation together are bounded
to 120 seconds. A saved route is reused when generation is retried. Up to three
claims are allowed, including crash recovery. Provider/timeout failures retry
after 2 then 4 seconds; expired leases can be reclaimed. Schema/source-reference
failures stop immediately. Cancelling or replacing the lease makes a previous
worker's result ineligible for publication. This can repeat a billed model call,
but cannot publish two artifacts for one task. External writes need a separate
approval/reconciliation service and are not part of this worker.

Before generation, the worker compares saved workflow, prompt and configuration
fingerprints with its runtime. A mismatch fails with `release_unavailable` instead
of silently generating with a different release. Restore the matching worker to
process still-queued work; for an already-failed task, submit a new request ID after
reviewing configuration. There is no mutation/retry endpoint for terminal tasks.

New requests pin `contextual-task-1.0.0` in `app/assistant/routing.py`, including
routing policy/schema and small-model configuration plus summary preferences.
Older `summary-task-1.0.0` requests still use their original prompt unchanged. AI changes
must update the release/version and replayable evaluation evidence. Results record
the actual provider/model as well as pinned fingerprints. An inference cache is
not implemented; the old summary cache is deliberately not read by this path.

## Failure investigation

| Task/error | Check and recovery |
|---|---|
| Queued for a long time | Worker process, matching DB configuration and database connectivity. Start/recover worker; do not resubmit repeatedly. |
| Running after a crash | Allow lease expiry; a replacement worker can claim it within the three-attempt budget. |
| `upstream_model_unavailable` | Provider/model access, configured model ID, timeout and credentials. No raw provider error body is exposed. |
| `invalid_summary_output` | Replay the synthetic case and inspect a controlled evaluation run. Invalid IDs or malformed JSON never become published artifacts. |
| `release_unavailable` | Align API/worker code and model configuration; a failed task needs a fresh request. |
| `attempts_exhausted` | Repeated worker interruption; restore worker stability before submitting a fresh request. |
| `idempotency_conflict` | Reuse a request ID only for byte-equivalent canonical request content; a changed snapshot/instruction needs a new ID. |

Current limitations: model quality is not proven by adapter tests; source citations
validate membership rather than semantic entailment. Gmail source completeness,
retention jobs, worker metrics/alerts, live SSE following and frontend review are
still required before a production rollout. A matching registered Bedrock model
and AWS test-account smoke test are not provisioned by this implementation.

## Verification

Use a disposable PostgreSQL 16 database. Existing tests drop/truncate tables in
the selected test database; the migration test additionally creates and drops a
uniquely named temporary database and therefore requires test-only CREATEDB access.

```bash
cd backend
THREADLY_REQUIRE_TEST_DB=1 python -m pytest -q
python -m ruff check app tests
```

Set `THREADLY_TEST_DB` to the disposable database URL before running. Test setup
ignores the application's `DATABASE_URL` and forces synthetic provider selection
so a developer's Bedrock environment does not accidentally invoke cloud inference.
Individual adapter tests opt into injected fake providers. CI provides
PostgreSQL and now fails if it is unreachable instead of silently skipping the
integration suite. Tests include concurrent duplicate requests/claims, lease
replacement, cancellation during inference, retry exhaustion, source pinning,
owner isolation, event replay, artifact schema validation and migration upgrades.
Model responses in these tests are synthetic; no real mail is sent or cloud
inference billed by the test suite.
