# Engineering quality gates for every implementation slice

These are acceptance standards for humans and coding agents, including Codex 5.3
Spark. They cannot make two models identical, but they make errors more visible
and completion evidence comparable. Do not lower a gate to meet an agent-hour or
calendar estimate. A smaller labelled release is preferable to false completion.

## Q1 — Before editing

Read root/local AGENTS, the selected B card, current API/data model and affected
code/tests. Identify the invariant being changed and what must remain compatible.
State one user-visible outcome, likely files, migration and provider dependencies.
If the proposed file is absent, create it deliberately; do not treat a plan path
as existing code. Identify one concrete happy path and the key failure paths first.

Fetch/check the actual merge base when available. Use `codex/<slice>` feature
branches, not direct commits to the integration branch. Preserve unrelated changes.
Coordinate migration head when other work lands; never run destructive resets to
avoid a conflict. If the frontend/provider configuration is unavailable, define
and test the backend seam and label the integration dependency precisely.

## Q2 — Implementation standard

- Keep endpoints thin: auth/strict input, service call, transaction and response.
  Keep deterministic validators, provider adapters and orchestration separate.
- Reuse current FastAPI `ApiError`, async SQLAlchemy sessions, httpx transports,
  lazy heavy SDK imports and configuration patterns. No new framework just to
  route five intents. New dependencies must solve a concrete need and be documented.
- Use explicit enums/typed schemas for boundaries; unknown keys/IDs/operations fail.
  Keep primary ownership/state/version fields queryable, with JSONB for typed payloads.
- Avoid catch-all success fallback, silent truncation, mutable default arguments,
  blocking network on the event loop, raw model output logging and dynamic tool URLs.
- Database changes include Alembic migration, model parity and updated docs. Do not
  use `create_all` to replace migrations in application startup.
- Preserve initial task request hashes and historical prompt/releases. Breaking
  input/output changes require explicit contract/version migration and fixtures.
- No unbounded retrieval, pagination loop, tool loop, model repair loop or bulk
  background task creation. Bound memory, concurrency, time and retry counts.
- Comments explain invariants or non-obvious choices; don't narrate obvious code.
  No TODO/501/fake success left on the assigned task's claimed completed path.

## Q3 — Required test families

The selected task card provides concrete assertions. Use deterministic test data,
fake clocks/controlled awaits and mock transports; avoid brittle sleeps. Test
observable contracts and actual failure behavior, not implementation-shaped mocks.

| Risk | Required evidence when touched |
|---|---|
| Ownership | Two-user reads/mutations, spoofed references, direct DB composite-FK tests for new owned tables |
| Idempotency | Same key/same normalized input replay; same key/different input conflict; concurrent duplicate acceptance |
| Versions/races | Stale save/approval/selection, competing updates, cancellation during blocked work, expired lease fencing |
| Transactions | Failure before commit leaves no orphan job/approval; no DB locks held during provider calls; helper commits cannot leak unrelated changes |
| External writes | Before/after-dispatch crash, timeout after simulated acceptance, late response, ambiguous provider result and read-before-retry recovery |
| Source trust | Prompt injection inside source, invented IDs/recipients, stale/deleted context, exact ordinal map, partial coverage |
| Time | UTC/local conversions, anchor persistence on retries, DST gap/fold, buffers, adjacent/overlapping intervals, unknown calendars |
| Schemas/MIME | Extra keys, control/header injection, malformed provider output, encoding/size limits, round-trip parsing independent of builder |
| Migration | Empty install, representative old mailbox/task/draft/review/action data, model drift, legal downgrade and guarded destructive downgrade |
| Compatibility | Old queued release prompts/handlers, old request hashes, legacy stub behavior until safely replaced, original artifact IDs preserved |
| Privacy | Synthetic secret/mail markers absent from errors/logs/events/traces; new egress paths use the established policy |
| Product truth | Saved/inserted/sent/booked differ; classification != execution; review != Send approval; unknown != failed/not sent |

Do not rely solely on a mocked service returning the expected dictionary. At least
one integration test should cross route → service → real PostgreSQL for new API
state. Provider fixtures verify transport behavior; live accounts validate provider
semantics. Model fixtures verify output rejection/control flow, not language quality.

For high-risk action, auth, continuation, artifact-stream, time and read-bridge
changes, do a focused second review of the diff after tests pass. A human or another
model may review where available; do not infer that this pack authorizes spawning
agents, sending messages, or dispatching work to another task. The implementer can
perform and record the review locally before presenting the PR.

## Q4 — Reproducible test commands

Use the checked-out `backend/pyproject.toml` and current Python 3.12+ environment.
Do not depend on the historical `/private/tmp/threadly-implementation-venv` existing
on another computer. Install the project's dev dependencies using the team's
supported environment setup. Current CI uses `pip install -e '.[dev]'` in backend.
Heavy optional dependencies are not needed to claim core service correctness.

Example disposable DB setup (choose an unused name/port; test credentials only):

```bash
docker run --rm -d --name threadly-handoff-test-db \
  -e POSTGRES_USER=threadly -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=threadly_test -p 127.0.0.1:55439:5432 postgres:16
docker exec threadly-handoff-test-db pg_isready -U threadly -d threadly_test
```

Wait for readiness before tests. If the chosen port is occupied, select another
and update the DSN; do not stop a database belonging to another task. Run from
`backend/` with the selected Python environment:

```bash
THREADLY_REQUIRE_TEST_DB=1 \
THREADLY_TEST_DB=postgresql+asyncpg://threadly:change-me@127.0.0.1:55439/threadly_test \
python -m pytest tests/test_draft_review.py -q

THREADLY_REQUIRE_TEST_DB=1 \
THREADLY_TEST_DB=postgresql+asyncpg://threadly:change-me@127.0.0.1:55439/threadly_test \
python -m pytest -q

python -m ruff check app tests
```

Replace the focused test file with the existing/new relevant tests for the task.
From repository root, additionally check each changed migration with Ruff,
`git diff --check` and the documentation validators:

```bash
python3 docs/implementation-playbook/tools/build_task_cards.py
python3 docs/implementation-playbook/tools/validate_plan.py
python3 docs/backend-execution/tools/build_cards.py --check
python3 docs/backend-execution/tools/validate_handoff.py
```

`make lint` and `make test` are existing shortcuts, but they use the selected
`python` and do not themselves guarantee the DB-required environment variables.
`backend/tests/conftest.py` drops/truncates tables. **Never use a live mailbox DSN.**
Migration tests also create temporary databases; use an isolated role/database
instance with those permissions rather than raising production credentials.
After tests finish, stop only the disposable container created for this run:

```bash
docker stop threadly-handoff-test-db
```

Run focused checks while iterating and one appropriate settled full suite. Repeat
only affected checks after a subsequent edit, then full regression if risk warrants.
Report actual pass/fail/skip counts. Baseline 233 is a historical comparison, not a
hardcoded target or reason to delete new tests. A green run with skipped DB cases
cannot verify persistence/concurrency/migrations. Unavailable infrastructure is a
reported validation gap, not proof the implementation works.

For documentation-only changes, validate docs/links/cards and diff formatting;
do not claim a fresh backend run. Current CI is path-filtered and may show no jobs
on a docs-only PR. “No checks triggered” is not “CI passed.”

## Q5 — Model and Flow changes

Pin prompt/schema/tool/flow/model configuration hashes. Keep old release assets
reachable for queued work and retries. Every changed router/generation flow has
versioned replay cases: valid output, ambiguous/missing inputs, malformed JSON,
extra fields, adversarial source content, unsupported compound plan and invalid
source IDs. Add a live evaluation report only when it actually ran against the
specified model/profile/release with authorized budget/data.

Do not guess numerical success rates. Agree evaluation thresholds with the team
before running the release holdout; record denominator, categories, failures,
manual adjudication and uncertainty. No comparison to Astra/Superhuman is justified
without a designed comparison and measurements.

## Q6 — Checkpoint and delivery

Each completed implementation PR includes a task checkpoint, runtime docs, API
examples/configuration and migration notes. Use [CHECKPOINT.md](CHECKPOINT.md).
Keep planned, implemented, locally tested, live validated, reviewed and deployed
states distinct. Original T-package statuses have wider acceptance criteria.

Before opening a PR, inspect the full diff and remove accidental files/secrets,
unrelated formatting and misleading completion claims. Use a description about
what changed and why, validation, migration and limits. Wait for applicable CI and
record its result. Do not auto-merge. For the next task, verify the merge and read
its checkpoint rather than relying on conversation memory.

Local code/tests/docs and an authorized commit/push are routine work. Live email,
Calendar writes, production migration/deployment and billed cloud operations need
explicit authorization for that environment. Prepare the exact run/configuration
and expected effects first so any approval concerns a concrete reviewable action.
Do not repeatedly ask for authorization already granted in the session.
