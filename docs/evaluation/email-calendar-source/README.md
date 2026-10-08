# Conversational email-to-event bridge

This local candidate addresses the 8 October 2026 inspection incident: the
production tool accepted only user-message provenance, rejecting freshly read
email facts and asking for known title/date information. The replay uses a
synthetic property-inspection notice (15 October when run on 8 October), a
synthetic address, Australia/Melbourne and the saved 30-minute duration.

`contextual-conversation-1.9.0+email-event.1.json` freezes the prompt and tool
contract. The previous chat-context assets remain unchanged.

## Replay

Run with Python 3.12 and a disposable PostgreSQL database:

```sh
THREADLY_TEST_DB=postgresql+asyncpg://threadly@localhost/threadly_test \
THREADLY_REQUIRE_TEST_DB=1 python -m pytest \
  tests/test_calendar_email_context.py tests/test_calendar_email_context_boundaries.py \
  tests/test_calendar_email_context_review.py -q
```

The four initial cases failed before implementation, with both entry paths
(summary follow-up/direct selected email) crossed with Ask/Always approval.
Boundary cases cover user correction, genuinely missing time, multiple events,
ambiguous numeric dates, unread/forged evidence, date/time contradictions, source
instructions, stale/disconnected/changed accounts, ownership, independent goals,
review restoration, exact approval, duplicate requests and fresh worker checks.
Model decisions and Google transports are deterministic fakes; no live inference
or Google writes were performed. These results prove backend contract and
lifecycle behavior, not Haiku extraction quality. A bounded live evaluation of
both flows, source-faithful title selection and ambiguity identification remains
necessary before making any model-quality claim. Relative and yearless email
dates currently clarify rather than assume an anchor or year.

## Independent review regressions

The read-only architecture review found five issues in the first candidate. The
local patch addresses each with database-backed offline coverage:

- `test_short_clock_quote_cannot_drop_email_timezone` and
  `test_event_quote_boundary_cannot_cut_off_a_clock_qualifier` preserve AEST/AEDT.
  Unknown timezone clarification stays required across turns; explicit user
  timezone correction wins.
- `test_repeating_accepted_generated_title_preserves_its_provenance` accepts an
  unchanged generated title while completing missing details.
- The `blank_quote` boundary case rejects normalized-empty title evidence;
  normalized-empty event evidence is rejected before matching.
- `test_source_field_repair_preserves_other_verified_fields_without_resupply`
  repairs a bad date or location without asking again for valid siblings.
- `test_durable_calendar_receipt_review_survives_lost_gmail_access` verifies
  succeeded and outcome-unknown receipts through replay, chat review and GET.
  Fresh mail remains mandatory for proposed/approved actions and worker dispatch.

The initial complete suite had 2,470 passes and one existing feedback assertion
failure: a user-only schema error mentioned email reading. Feedback now mentions
email source repair only when the rejected call actually supplied that envelope.
Final exact-commit validation is recorded with the task's evidence bundle.

The second review identified two continuation regressions. Against clean commit
`6da4c4a`, three exact multi-turn cases fail: a later location edit repeats source
08:30 after the user chose 09:00; a later title edit restores a user-cleared
location; and a shorter fresh source envelope clears unresolved CST. All three
pass after protecting retained user provenance and carrying timezone clarification
forward. The focused source/approval/field-repair set passes 53 tests. The 6da4c4a
full suite passed 2,482 tests, but does not validate these subsequent fixes; the
final commit must pass its own full suite before the patch is reported verified.
