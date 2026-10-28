# Combined voice/reply and security-recognition review

This local branch combines released maintenance base `1cb7328` with voice/reply
`d81d26e` and security recognition `150683a`. They were cherry-picked in a fresh
worktree as `2f31732` and `22d72c2`. The shared checkout and original task branches
remain untouched. The frontend companion combines the matching changes on `4fe4f32`.

## Review and correction

The original changes overlap only in `docs/api-contract.md`: the optional security
priority section and the reply-role/grounding section both survived unchanged.
Classification uses its own decision schema, numbered prompt and provider adapter;
conversation/draft generation uses its own versioned instructions and schemas.
No runtime interaction defect was found in the combined source and regression checks.

Running previously skipped database tests found that the two new reply replays
omitted the required `AssistantRequest.continuation`. Commit `2aac309` supplies
`continuation=None`; this corrects the test fixture, not runtime behavior. Both
cases then passed through actual source capture, durable routing/worker execution,
artifact/envelope validation and duplicate submission. Their zero send/save/approval
row assertions passed. The original source branches were not amended.

## Combined results

From `backend/`, using the existing local Python 3.14 environment:

```sh
python -m ruff check app tests
THREADLY_TEST_DB=<disposable-postgresql-url> THREADLY_REQUIRE_TEST_DB=1 \
  python -m pytest -q -ra
```

**2,482 passed, zero skipped**, in 490.54 seconds. Ruff and `git diff --check` pass.
The two Starlette/httpx deprecation warnings are non-failing. This full run includes
the PostgreSQL API, worker, ownership, source-freshness, approval and migration cases
that were skipped in the earlier isolated reports. Those earlier skip limitations
are superseded by this result. The earlier combined no-DB run had 1,493 passes and
989 skips; it is retained as separate historical evidence, not the acceptance result.

PostgreSQL 16 ran in a newly created disposable Docker container from the existing
local `postgres:16-alpine` image, with no pulls, tmpfs data, a random loopback-only
port and no mounts or existing database access. The run required DB availability
instead of silently skipping. Tests used synthetic Gmail/model adapters throughout.
The container was stopped with automatic removal after the test process completed.

The matching built frontend passes TypeScript, **336 unit tests** and **20 packaged
Playwright tests**, including voice/reply, security recognition, inbox layouts,
session/audio races and draft-card consent/edit behavior. Browser profiles are
disposable and closed; no shared interactive browser was used.

## Remaining gates

These checks establish local implementation and integration behavior, not model
quality. No paid inference, real mailbox reads/writes, email sending, provisioning,
publication, deployment or shared-branch merge was performed. Recognition prompt
1.0.2 still needs held-out model acceptance and versioned managed prompt/Flow
provisioning; drift guards must not be bypassed. The new reply prompt likewise has
no paid model-quality evaluation. The original incident screenshot could not be
downloaded through its supported Library flow and was never viewed; synthetic QA
images do not substitute for that missing incident evidence.
