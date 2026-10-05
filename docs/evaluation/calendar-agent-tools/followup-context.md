# Calendar follow-up context — conversation 1.4.1

The reported conversation lost its date when `check now` followed a partial
availability answer. The model could see the original user wording, but the
backend required date provenance from only the latest turn. Old Calendar answer
text is deliberately hidden from model history, so it could not safely recover
an authoritative date from the displayed answer either.

## Change

One bounded, encrypted Calendar request retains the original user instruction,
typed arguments and date anchor. `retry_calendar_read` repeats that request with
current preferences and fresh evidence. Whole-day results pin their resolved
civil date, including across midnight and a timezone preference change. The read
clock remains fresh for expiry and remaining-day clipping. A new date replaces
the request; an unrelated completed topic clears it. Existing chats can recover
user-authored intent where their original date anchor is unambiguous. Recovery
never trusts provider prose or repeats an external write.

## Verification

- Focused Calendar/conversation suite: **223 passed, zero skipped**, with disposable
  PostgreSQL 16 (23.02 seconds). Two local Python 3.14 dependency deprecation warnings.
  Includes the reported exchange, repeated retries, midnight/past dates, preference
  changes, connection errors, clock/duration preservation, cancellation/new
  constraints, legacy recovery, encrypted persistence, ownership and idempotency.
- Full backend Ruff check and `git diff --check` passed.
- `live-followup-v1.json`: **6/6 live Bedrock follow-up decisions passed** using
  the deployed Haiku profile and the versioned prompt/tools. The initial Calendar
  request is seeded through real runtime/date handlers; follow-up decisions are
  live model calls. Calendar preferences/freebusy are synthetic, all external
  writes are disabled, and no Google/Gmail adapter is called.
- `contextual-conversation-1.4.1.json` pins prompt/tool hashes. `replay-v3.json`
  versions the preexisting deterministic Calendar tool cases for this release.
  Historical 1.4.0 assets and receipts remain unchanged.
- Full PostgreSQL backend CI is required on the exact PR head before merge/deploy;
  its result is recorded by the PR checks, not inferred from these focused results.

Replay the new live decision gate from `backend/` with the existing authorized
Bedrock configuration, external writes disabled and a synthetic-only environment:

```sh
python -m app.conversation.calendar_followup_evaluate --live --output /tmp/followup.json
```

No migration, Google scope change or extension package is required. A provider
failure on a selected holiday calendar still produces incomplete coverage; this
change remembers what to recheck and does not hide that failure. Legacy relative
requests with ambiguous original date bounds need one explicit date clarification.
