# Calendar read foundations — B12

This release adds authenticated Calendar listing, explicit scheduling preferences
and saved free/busy evidence. It does not calculate slots, generate scheduling
replies, reserve time or create events. The model receives no Calendar credential
or tool here. B13 can consume this backend-owned evidence after freshness checks.
Frontend integration and live Google validation are pending.

## API contract

Every Calendar route requires the existing Threadly JWT. User identity comes from
that JWT; there is no user ID, provider URL, arbitrary scope or model input field.
Successful responses carry `Cache-Control: no-store`.

| Route | Request | Response |
|---|---|---|
| `GET /calendar/calendars` | None | `account_version`, `checked_at`, `calendars` |
| `GET /calendar/preferences` | None | Saved `version`, `account_version`, `policy_version`, `preferences`; 404 before first save |
| `PUT /calendar/preferences` | `expected_version`, complete `preferences` | Same saved preference shape; first save uses version 0 |
| `POST /calendar/freebusy` | `expected_preferences_version`, offset-aware `start`, `end` | 201: saved evidence; always uses the authenticated owner's selected calendar set |
| `GET /calendar/freebusy/{evidence_id}` | UUID | Same evidence while current/unexpired; owner mismatch 404, stale/expired 409 |

A list item contains `id`, `summary`, `access_role`, `can_read_busy` and
`event_write_acl`. The last field reports the observed **ACL only**, not an OAuth
grant, installed booking capability or approval. A reader/freeBusyReader calendar
is selectable for busy checks even though it is not writable. Unknown roles and
deleted entries are not selectable. Hidden subscribed calendars are included.
IDs must come from the current account's CalendarList; group IDs/arbitrary external
attendee addresses and the `primary` alias are not accepted unless actually listed.

Example first save (all policy choices are explicit user inputs, not silent defaults):

```json
{
  "expected_version": 0,
  "preferences": {
    "timezone": "Australia/Melbourne",
    "calendar_ids": ["owner@example.test"],
    "working_periods": [
      {"weekday": 0, "start_minute": 540, "end_minute": 1020},
      {"weekday": 1, "start_minute": 540, "end_minute": 1020}
    ],
    "buffer_before_minutes": 10,
    "buffer_after_minutes": 10,
    "minimum_notice_minutes": 60,
    "default_duration_minutes": 30
  }
}
```

The calendar ID above is illustrative: choose an ID returned for the real account.
Weekday 0 is Monday. Minutes are local wall-clock minutes after midnight; 1440 is
allowed only for an end. Split overnight work into two day entries. Overlapping
periods are rejected; adjacent periods are valid. Saving advances the version even
for identical content. There is no implicit calendar/timezone/working-day default.
Current policy is `calendar-read-1.0.0`; preferences must be re-saved after an account
permission version changes. GET preferences remains available to review old settings.

Example free/busy body (use current future dates when testing):

```json
{
  "expected_preferences_version": 1,
  "start": "2026-10-01T09:00:00+10:00",
  "end": "2026-10-01T17:00:00+10:00"
}
```

Evidence contains `id`, `preferences_version`, `account_version`, `policy_version`,
`checked_at`, `expires_at`, normalized UTC `start`/`end`, aggregate `coverage`
(`complete` or `unknown`) and `calendars`. Each calendar has `calendar_id`,
`status` (`known`/`unknown`), nullable bounded `reason`, and `busy` intervals.
Intervals use inclusive start/exclusive end, are clipped to the requested window,
sorted and merged within each calendar. Empty busy intervals with **known** status
mean Google reported none; empty intervals with **unknown** status prove nothing.
There is deliberately no `is_free`, slot or booking-success field.

Provider errors, omitted calendars, malformed intervals and a calendar removed
from the current list produce explicit unknown coverage. Other calendars can keep
their valid intervals, but B13 must reject an overall availability claim while any
selected calendar is unknown. Top-level HTTP/shape/limit failures return an error,
not a stored successful evidence record. Provider bodies are never returned/logged.

## Consent and capability mapping

Normal login remains identity + Gmail read. Existing authenticated reconnect now
accepts optional `capabilities: ["calendar_read"]` alongside `redirect_uri` and
`code_challenge`; omitted capabilities preserve the original request behavior.
Only named Gmail/Calendar read capabilities are allowed. Send/write scope requests
are rejected. Existing state/PKCE, exact redirect and same-subject checks still apply.

Calendar read reconnect requests these two scopes:

- `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
- `https://www.googleapis.com/auth/calendar.events.freebusy`

Only scopes actually returned by Google are stored. Declining either permission
leaves Calendar reads unavailable. Existing refresh-token preservation and reduced-
grant handling continue through B01. No granted permission is inferred from consent
intent. A normal token refresh does not invalidate evidence unless grants change.

`GET /assistant/capabilities` adds `calendar_list` and enables installed
`calendar_read`; Calendar service routes require **both** ready. Each capability's
`any_of_scopes` remains alternatives within that capability, not a combined AND list.
Full `calendar.readonly`/`calendar` grants can satisfy both. Freebusy-only grants
cannot authorize CalendarList. `calendar_write` and Gmail sending stay unavailable.
Readiness describes stored credentials/grants; it is not a live access probe.

References checked for this implementation:
[CalendarList roles, pagination and scopes](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list),
[free/busy scopes, intervals and partial errors](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query).
Google's endpoint/ACL semantics still require a controlled account smoke check.
The declared first-party `tzdata` dependency provides an IANA fallback on hosts
without system timezone data, following [Python ZoneInfo guidance](https://docs.python.org/3/library/zoneinfo.html#data-sources).
B13 still owns date resolution and DST gap/fold behavior.

## Transaction lifecycle and limits

```mermaid
sequenceDiagram
    participant C as Authenticated client
    participant API as Calendar service
    participant DB as PostgreSQL
    participant G as Google Calendar
    C->>API: Query window + expected preference version
    API->>DB: Read account/grants and preferences, close transaction
    API->>API: Obtain token, recheck permission version
    API->>G: Bounded CalendarList read, then selected freeBusy query
    G-->>API: ACLs and per-calendar busy/errors
    API->>API: Validate, clip, merge; preserve unknown coverage
    API->>DB: Lock account then preferences; recheck versions and expiry
    alt Unchanged and fresh
        API->>DB: Persist owned immutable evidence, commit
        API-->>C: Evidence ID, intervals, coverage and expiry
    else Changed or expired
        API-->>C: Conflict; no evidence saved
    end
```

No transaction spans token refresh or Calendar HTTP. Initial preference creation
and subsequent updates serialize on the account row then preference row. Expected
version comparisons happen again after provider reads, so competing updates have
one winner. Evidence reads also recheck account/preferences/policy and expiry.
A Google event or ACL can change immediately after a read; five-minute freshness
is evidence age, not a reservation or a guarantee of continuing remote access.
Slot selection/booking must perform a fresh check later.

B13 must query a **padded evidence window**: extend the desired search start by the
saved after-event buffer and the search end by the before-event buffer. Busy events
just outside the desired slot window can still block it after applying buffers.
This API clips only to its actual query window; it must not be asked to guess busy
periods outside that window. The bounded 245-minute lookback allows the maximum
240-minute buffer plus five minutes of clock/request margin for near-term slots.
Keep the padded window within the 31-day query/90-day horizon bounds.

Fixed release bounds (constants in `schemas/calendar.py`, `calendar/client.py` and
`calendar/service.py`; no new environment switches):

| Bound | Value |
|---|---|
| Selected calendars | 1–10 distinct IDs, max 1,024 characters each |
| Calendar list | 10 pages × 100 entries; repeated tokens/duplicate IDs rejected |
| Query duration | Positive, at most 31 days |
| Query horizon | Start no earlier than server time minus 245 minutes; end within 90 days |
| Intervals | At most 2,000 across requested calendars before clipping/merging |
| Provider response | 2 MB decoded per call; fixed Google URLs, no redirects/retries |
| Network | 10-second HTTP timeout; list total 30 seconds; freebusy total 15 seconds |
| Evidence lifetime | 5 minutes from pre-fetch database timestamp, checked again before save/read |
| Working periods | 1–28, weekdays 0–6; nonoverlapping local periods |
| Buffers / notice / duration | Each buffer 0–240 min; notice 0–10,080 min; duration 5–480 min |

Errors retain the standard envelope. Main codes: 403 `calendar_connection_required`
or `calendar_access_denied`; 404 `calendar_preferences_missing` /
`calendar_evidence_missing`; 409 `calendar_context_changed` /
`calendar_evidence_expired`; 422 `validation_error` / `calendar_not_selectable` /
`calendar_window_invalid`; 502 `calendar_response_invalid` / `calendar_list_limit` /
`calendar_interval_limit`; 503 `calendar_unavailable`. Existing auth refresh errors
can also propagate, sanitized. No successful fallback to guessed availability.

## Migration, testing and next implementation

Migration `c12026e9a032` follows `b10c026e9a31`; apply with matching API/workers.
It creates owned preferences and evidence, preserving prior mailbox/tasks/actions.
Database guards enforce advancing preference versions and immutable evidence.
Downgrade refuses while either table has user data. Backups and normal deployment
sequencing apply; this PR does not deploy or activate any cloud resource.

Evidence stores only calendar IDs, busy intervals, coverage, query window and
version/timing metadata. Titles/event contents/tokens are not persisted here.
Expiry prevents reuse; expiry is not deletion. Scheduled evidence retention, global
request quotas and operational throttling remain B19 gates before a broad pilot.
Each explicit POST makes a bounded new read; there is no automatic background poll
or generation-worker retry. Repeating POST creates another evidence record.

[Checkpoint and actual test results](backend-execution/checkpoints/B12.md).
No prompt/model/Flow changes or live calls were made. B13 must load owned fresh
complete evidence, resolve time with original request/context anchors, apply these
preferences and calculate deterministic slots (including DST tests). B14/B15 add
negotiation and exact approved event execution/recovery. Scheduling and Calendar
multi-intent templates must remain unsupported until those handlers exist.
