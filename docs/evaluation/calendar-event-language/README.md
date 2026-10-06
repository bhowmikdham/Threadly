# Calendar creation, choices and interrupted-chat recovery

Release `contextual-conversation-1.7.3` preserves the merged shared-mail context
with Calendar language/continuation fixes, a timed availability read, explicit
chat recovery and structured destination selection. Direct creation policy is
`direct-calendar-event-1.1.1`; Calendar reads are `calendar-conversation-reads-2.1.0`.

**Historical 1.7.2 verification:** 1,774 backend tests passed, zero failed/skipped, against isolated
PostgreSQL 16. Ruff (`app tests tools`), MVP asset checks, planning/handoff validators
and diff checks passed. Runtime tested: `2c9f5de7d79d3011af4056149d70106f5cfd2cbf`.
The JSON [verification record](verification-1.7.2.json) includes that release's asset
hashes and individual focused case IDs from that full run. Tests use synthetic
Google transports and scripted model decisions; this is not a live model-quality
or live Calendar acceptance score.

Replay from the repository root with the dev Python environment and an isolated,
disposable PostgreSQL instance (the fixture drops/truncates test tables):

```sh
THREADLY_REQUIRE_TEST_DB=1 THREADLY_TEST_DB=<isolated-postgresql-dsn> \
  python -m pytest -q backend/tests
```

The seven focused test files listed in the verification record cover spoken clocks,
time-first titles, missing-field continuations, editable/duplicate/reordered/stale
choices, click/ordinal/email selection, no model call for clicks, Ask/Always,
concurrent retries, account ownership, quoted instructions, lost-response recovery,
late-commit fencing, old-success/new-failure status, partial read coverage and date
anchors across midnight. Previously supplied relative dates retain their anchor;
newly supplied date words use the new turn's clock.

The original 1.5.1 and combined 1.7.1 snapshots/replays remain immutable. The final
1.7.2 snapshot remains unchanged; the new 1.7.3 snapshot matches runtime through
the asset equality test;
`replay-v3.json` combines the previous read fixtures and new single-start cases.
Historical 1.5.0, 1.6.0 and 1.7.0 files elsewhere in evaluation are unchanged.

No schema migration or new Google scope is required. Existing actions, approvals,
workers and uncertain-outcome reconciliation remain authoritative. The matching
frontend picker is [PR #93](https://github.com/bhowmikdham/Threadly/pull/93).
No real event creation/deletion, production mutation or new live-model evaluation
was performed for this follow-up.


A subsequent [exact-phrase regression](reported-phrase-kelly.json) passed separately
(1 test, no skips): `create me a event at 4pm tmrw for a meeting with kelly` at
00:17 Melbourne on 6 October resolves to 16:00 on 7 October. A wrong Calendar-read
decision is rejected; the typed event retains the literal title, no inferred attendee
email, no notifications and Ask state. No runtime/prompt/tool change was required.
This additional test is **not included** in the earlier 1,774-test total. Installed
extension/server version remains unconfirmed; this follow-up was not deployed.


The 1.7.3 follow-up recognizes leading “help me create” requests under the existing
source boundary, returns `calendar_choice_unavailable` when a request expires
while calendars are loading, and freezes all creation clocks in midnight replays.
[Replay and verification](reported-phrases-1.7.3.json) covers the exact Ashu request,
three synthetic 429s followed by the same-request retry, no invented attendee,
Ask preservation, quoted/content negatives and expiry in Ask/Always. The model
still chooses typed tools; the backend validates fields, permission and action state.
The live provider's 429 capacity issue is not resolved by these local guards.
