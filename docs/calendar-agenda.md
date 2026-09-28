# On-demand Calendar agenda

Threadly reads event details from a person's selected calendars only when that
person asks for their agenda. It first checks CalendarList metadata and ACLs for
the account's available calendars so it can verify those selections. It does not
subscribe to Calendar changes, import all events or maintain an event cache.
Availability and slot proposals remain separate free/busy reads;
an event title is never evidence that a time is free or permission to book it.

## Connection and selection

Google sign-in identifies each account by the verified Google subject. The existing
Calendar connection grants CalendarList and free/busy access for scheduling. Viewing
event names requires the separately requested `calendar_events_read` capability
(`https://www.googleapis.com/auth/calendar.events.readonly`). An authenticated
reconnect may ask for it; a scope in the request does not count as a grant until
Google actually returns it. A Calendar write grant and exact event approval are
separate. The account must save explicit Calendar preferences with one to ten
selected calendars and an IANA timezone before an agenda read.

## Direct API and chat

`GET /calendar/agenda?period=today` is authenticated and takes one of `today`,
`tomorrow`, `this_week` or `next_7_days`. The first two use the selected timezone's
local day. `this_week` is Monday through the next Monday; `next_7_days` starts
with the current local day. The response contains the checked window, account and
preference versions, a `checked_at` timestamp, `coverage`, and per-calendar
events/coverage. Its response is `Cache-Control: no-store`.

The conversation `read_calendar` tool accepts the same periods. It can answer a
direct agenda question using a deterministic rendering of the checked result;
it cannot calculate availability, draft an invite, send mail or book a meeting.
The tool may not silently change the requested period. Scheduling requests use
the existing reviewed proposal and fresh free/busy/slot workflow.

Google `events.list` is called once per selected readable calendar, for the chosen
window and with a maximum of 25 events per calendar. At most 50 events appear in
one response; the chat answer displays at most 10 of those. Pagination is not
followed. A continuation token or local result cap marks coverage `partial`.
Inaccessible or failed calendars are `unknown`, and the answer does not claim an
empty or complete agenda when any selected calendar is partial or unknown. A
Google event marked private/confidential is returned only as `Busy`, without its
location. Account or preference changes during the provider read invalidate it.

The direct result is transient. The conversation response and normal bounded,
encrypted conversation history can retain rendered event titles for up to seven
days so a user can review the answer; no raw `events.list` payload, event cache or
background Calendar sync is added. Delete a conversation to remove its retained
chat history earlier. Do not use Calendar event text as instructions to a model.
Later model turns receive only a marker that an agenda was shown, not its provider
authored titles or locations; a follow-up needing current event details reads
Calendar again.

## Acceptance boundary

Synthetic provider and two-account ownership tests check bounded reads,
partial/unknown coverage, private-event redaction, account/preference fencing and
the conversation tool. A live two-account comparison with Google Calendar is still
needed before declaring public readiness. Public rollout still requires verified
Google consent and an HTTPS API.

Provider contract: [Google Calendar events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)
and [Calendar authorization scopes](https://developers.google.com/workspace/calendar/api/auth).
