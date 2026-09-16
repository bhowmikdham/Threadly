# Deterministic Calendar slots — B13

B13 adds a direct authenticated read API that calculates available times from B12's
saved preferences and Google busy evidence. No model calculates dates or availability.
This API is not yet registered as a scheduling step in `/assistant/requests` or the
compound planner. B14 connects negotiation and slot-grounded drafting; B15 adds exact
approved event creation and reconciliation. A slot is **not a reservation**.

## Request and response

First connect actual Calendar list/freebusy grants and save explicit
[preferences](calendar-reads.md). Identity comes from the authenticated JWT. All routes
use `Cache-Control: no-store`. Selected calendar IDs come only from those preferences.

`POST /calendar/slot-requests` returns **202** with the current persisted receipt.
The initiating request normally completes its bounded provider read before returning;
202 does not imply a queue worker. A concurrent duplicate can see `processing`.

```json
{
  "request_id": "meeting-options-1",
  "expected_preferences_version": 1,
  "date": "tomorrow",
  "days": 1,
  "duration_minutes": 30,
  "count": 3,
  "participant_timezones": ["Asia/Kolkata"]
}
```

Exact-time check (one result at most, even if count defaults to three):

```json
{
  "request_id": "check-four-1",
  "expected_preferences_version": 1,
  "date": "tomorrow",
  "at_time": "4",
  "time_context": {"start_minute": 900, "end_minute": 1080}
}
```

The context above means the user supplied an afternoon window, not a window chosen
because the calendar happened to be free. A future natural-language adapter must bind
this context to the user's request or confirmed inputs, never arbitrary email text.
Without explicit context, saved working hours may select one AM/PM interpretation if
exactly one fits the full duration and both clock interpretations exist. Otherwise
`needs_clarification` returns a reason and typed choices, without contacting Google.
The selected interpretation and its source are displayed in `resolution.assumptions`.

`GET /calendar/slot-requests/{UUID}` retrieves an owned fresh receipt. Main fields:

| Field | Meaning |
|---|---|
| `id`, `state` | Durable query UUID and processing / needs_clarification / complete / unknown / failed |
| `anchor_at`, `anchor_source`, `anchor_from_request_id` | Original server acceptance time, or an owned previous request's anchor |
| `preferences_version`, `account_version`, `policy_version` | Immutable source versions; slot policy `calendar-slots-1.0.0` |
| `created_at`, `expires_at`, `calculated_at` | Receipt lifetime and calculation time; calculation is null before publication |
| `resolution` | UTC search/evidence bounds, exact-time flag, zone, duration, assumptions, or typed clarification |
| `evidence_id` | Owned B12 evidence UUID; its GET API supplies checked_at, coverage and busy periods |
| `slots` | Up to three stable options, each with UUID, UTC start/end, IANA zone, offset-aware local labels and participant display labels |
| `reason`, `error_code` | Insufficient options, elapsed window, unknown coverage, or sanitized failure code |
| `availability_scope` | Always `user_selected_calendars` |
| `reservation` | Always false |

A terminal `complete` result may contain zero or fewer slots. It is successful
calculation, not successful booking. `unknown` returns no options; one inaccessible,
omitted, malformed or erroring selected calendar prevents a confident free-time claim.
Participant zones only format these same instants: they do not prove attendee availability.

## Anchors, clarification and retries

- `date` is `today`, `tomorrow` or `YYYY-MM-DD`. Relative dates use the saved anchor
  in the explicit request zone, or the saved preference zone. They do not drift at retry.
- Missing zone/duration uses the user's saved explicit preferences, with assumption
  sources returned. No silent Melbourne/30-minute application-wide fallback is added.
- `at_time`: `4` or `4:00` can mean AM or PM; `4 pm`, `16:00` and two-digit `04:00`
  are explicit. Optional `meridiem: "AM" | "PM"` must agree with any suffix.
- An exact local time in a DST gap asks for another time. A fold returns both UTC
  choices with offset-aware labels; submit `fold: 0` or `fold: 1` to choose. Calendar
  free/busy never chooses a fold or AM/PM for the user.
- Answer by submitting the corrected **full request**, a new `request_id`, and
  `anchor_from_request_id` equal to the previous receipt UUID. Even after that receipt
  expires, its owned immutable anchor can be inherited. This does not revive old slots.
- There is no in-place free-text answer endpoint in B13. There is also no selected-email
  timestamp anchor adapter yet; B14 must distinguish message-relative from request-relative
  wording explicitly instead of accepting an arbitrary client timestamp.
- Same user + request_id + identical normalized body returns the same receipt/options
  without another Google read. A changed body with the same key returns 409
  `idempotency_conflict`. A changed preference version can fail freshness first.
- Process loss leaves a `processing` receipt until expiry; no invisible automatic
  re-read occurs. Poll GET, then create a new request after expiry, preserving the
  prior anchor if appropriate. A provider failure persists `failed` and returns the
  normal error envelope with `detail.slot_request_id`; replay returns that failed receipt.

## Deterministic calculation and bounds

Policy version 1.0.0 uses a 15-minute grid in the owner's working-hours zone for
flexible searches. Exact times may be off that grid. Enumerate feasible starts, then
choose the earliest up to three nonoverlapping alternatives. This is a deterministic
initial ranking policy, not a claim of optimal meeting distribution across days.

1. Resolve date and clock using immutable inputs. Clip elapsed parts of `today` and
   apply notice at calculation time; an elapsed window returns empty with `window_elapsed`.
2. Query from desired start **minus after-event buffer**, through desired end
   **plus before-event buffer**. This retains recently ended/upcoming events that
   could otherwise be clipped away before buffer expansion.
3. Require complete owned evidence for every selected calendar and the full padded
   window. Expand each busy interval in UTC by before/after buffers, then merge nested,
   overlapping and adjacent intervals. Half-open intervals allow exact boundary adjacency.
4. Construct UTC working windows by testing actual minutes against saved local weekly
   membership. Both repeated DST hours exist; missing local minutes do not. Split
   overnight preferences at midnight into consecutive weekday windows. Durations are
   elapsed minutes, not naive local-clock subtraction.
5. Enforce full-duration fit, minimum notice and no overlap with expanded busy periods.
   Adding busy time cannot create a feasible candidate. It may change the displayed
   top three by exposing a previously fourth-ranked candidate; test feasibility separately.
6. Persist the selected UTC instants and UUID5 option IDs under the query UUID.
   Replay reads stored options; it does not rerank or reinterpret them.

Input bounds: 1–14 local dates; exact-time checks use one date; 5–480-minute duration;
1–3 requested options; up to five distinct named participant zones. B12's preference
limits (10 calendars, 28 work periods, 240-minute buffers, 7-day notice) apply. Padded
reads stay within its 31-day duration / 90-day horizon / 245-minute lookback limits.
Out-of-horizon dates return clarification; malformed syntax/types return the existing
422 validation envelope. Missing preferences returns 404; missing actual grants 403;
missing or foreign receipt/anchor UUID returns 404.

## Freshness, storage and concurrency

```mermaid
sequenceDiagram
    participant U as Authenticated caller
    participant API as Slot service
    participant DB as PostgreSQL
    participant G as Google Calendar
    U->>API: Typed request + expected preference version
    API->>DB: Lock account then preferences; deduplicate; save anchor + receipt
    DB-->>API: Commit processing or clarification/elapsed result
    alt Clarification or elapsed
        API-->>U: Receipt; no provider call
    else Resolved window
        API->>G: B12 current ACL and padded freebusy (no open DB transaction)
        G-->>API: Known busy/empty or unknown coverage
        API->>DB: Save fenced evidence; lock account, preferences, receipt
        API->>API: Validate coverage/versions/expiry; calculate deterministic slots
        API->>DB: Commit immutable terminal result
        API-->>U: Slots / unknown / sanitized failure
    end
```

Receipt lifetime is at most five minutes from acceptance and never exceeds evidence
expiry or the earliest slot's start minus minimum notice. GET and replay check
account/preference/policy versions and expiry; stale offers return 409. A permission
or settings change during the provider read prevents publication. A failure rolls back
partial publication before marking the receipt failed. No database transaction spans
Google calls. Concurrent duplicate requests share one persisted lookup.

B12 evidence has no provider-wide change stream/version token. An external calendar
edit can occur within the five-minute lifetime. **B14/B15 must fetch fresh availability
before selection/booking**, bind the exact slot and current context, and refuse conflicts.
Do not describe an unexpired result as a lock on the calendar.

Migration `d13026e9a033`, parent `c12026e9a032`, adds owned `calendar_slot_requests`,
composite owner FKs for evidence/anchor, uniqueness and an immutability trigger. Only
processing → terminal publication is mutable, and expiry can only shorten. Downgrade
refuses while receipts exist. Expiry invalidates use; physical retention cleanup is B19.
No new environment variables, AWS resources, model permissions or event-write grants.

## Verification and next integration

Pure interval/DST/metamorphic fixtures, real PostgreSQL request/concurrency tests and
real Alembic constraints/drift/rollback tests are recorded in the
[B13 checkpoint](backend-execution/checkpoints/B13.md). Mock Google transport verifies
local behavior, not actual account consent, ACLs or Google data. Controlled live
freebusy comparison and team acceptance of ranking/grid/expiry policy remain open.

B14 should consume these stored slots and owned source versions, refresh on expiry or
source change, support typed selection, and generate prose only from backend-provided
labels. It must integrate the assistant task/clarification lifecycle explicitly. Do not
add Calendar steps to B10's allowlist until the full handler and dependency semantics
exist. Booking invitations remain B15 and require exact-payload approval.
