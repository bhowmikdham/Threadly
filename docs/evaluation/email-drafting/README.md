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

## Follow-up continuity (1.8.7)

The Oct 7 incident retained conversation history and the original drafting goal.
Rejected preparation calls fell back to prose, so no structured draft/card was
created and the goal remained in clarification. Subsequent prose promised a
Gmail save despite no conversational write tool. Narrow production inspection
found zero draft-save or email-send action rows during the incident. Rejected
tool arguments were not retained, so their exact invalid field cannot be proven.
The installed extension remains unverified; no card was returned by the backend.
Private transcript, account details and identifiers are excluded from this repo.

`test_email_draft_followup.py` replays the chain with synthetic identities,
scripted model decisions, real isolated database state, and `httpx.MockTransport`.
It covers complete structured drafting, repeated retained fields, invalid tool
repair, prose fallback rejection, bounded failure/retry, legacy user-turn recovery,
ambiguous recipients, permissions, missing-card guidance, repeated confirmations,
new goals, gratitude, compound requests, failed revisions, and success/failed/
uncertain save receipts. The simulated explicit card click is the only write;
chat turns create zero action/approval records and no duplicate provider call.
The frontend tests preserve local edits and verify exact versioned click payloads,
new-goal invalidation, old-server behavior, and restored-card versions.

`contextual-conversation-1.8.7.json` pins the prompt and tool schemas. The new
`review_email_draft` tool is read-only; no conversational save authority is added.
Live Bedrock behavioral evaluation and installed-client verification were not run.
No real Gmail/Calendar writes, grant changes, merge or deployment are part of this fix.
