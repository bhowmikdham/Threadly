# Conversational email drafting clarification

Reported case: repeated standalone drafting requests produced a preparation
acknowledgment followed by an unsupported card containing internal diagnostics.
The user transcript is bug evidence, not permission to send or save an email.

## Cause and change

The 1.8.3 conversation prompt directed missing-recipient composition straight into
`prepare_workflow`; its runtime returned “I’m preparing that for you.” The durable
router could reject incomplete content, while typed continuation only supports a
bounded field set (including recipient, not arbitrary purpose/content/subject).
The extension displayed `route.decision.rationale` verbatim for unsupported tasks.
This establishes the code path matching the report. The exact production receipt
was not retrieved: the authorized read-only AWS identity check failed because the
saved session expired. No authentication settings were changed.

The new semantic tool collects recipient and purpose from user turns before task
creation, retaining partial answers and current recipient authority. A named
recipient receives a text-only draft with generated subject. Explicit mailbox
recipients enter the existing worker with a backend-validated compose route;
neither route grants approval to send. New goals retire old recipient fields and
stale task pointers. Source-based workflows and external action gates are unchanged.

## Evidence

- `contextual-conversation-1.8.6.json` pins the complete prompt and tool schemas.
- `offline-replay.json`: 9/9 scripted turns through the production field/state
  validator, including reported variants, partial answers, repetition and named
  recipients. These are **offline tool replays**, not real-model quality results.
- `backend/tests/test_email_draft_clarification.py` covers persistence, idempotent
  retries, named recipients, corrections, Cc preservation, new goals, stale client
  pointers, cancellation, disabled sending and untrusted-source boundaries.
- Existing conversation tests still exercise draft artifacts, exact recipients,
  crash recovery, provenance and disabled Gmail sending through the new preflight.

Run from `backend` using a dedicated disposable PostgreSQL database:

```sh
THREADLY_TEST_DB=<isolated-test-database> THREADLY_REQUIRE_TEST_DB=1 python -m pytest -q
python -m ruff check app tests tools
python -m app.workflows.mvp_assets --check --output fixtures/mvp/prompts-v1.json
```

Live Bedrock evaluation and production conversation replay remain unverified.
No email/Calendar writes, external drafts, approval replay, merge or deployment
were performed for this fix. Backend draft stacks on Primary Inbox PR #107;
the extension companion preserves Primary Inbox PR #108 and Calendar PR #104.
