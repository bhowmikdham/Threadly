# Email-card Create event

Independent local implementation on released frontend base
`afa2180fa16b4060587002b6be440ce32573c603`. Not published or deployed.

`InboxCards` now offers **Create event** for a selected source email. The new
`MeetingEmailEvent` component sends only `{kind: "gmail_message", thread_id,
message_id}` to `POST /calendar/meeting-email/draft`. It shows the fresh source,
prefills the subject as an editable title, and requires date/time in the displayed
Calendar timezone. Other details remain optional. Attendees are not inferred;
entering addresses requires selecting the invitation checkbox.

**Review event preview** sends explicit edited fields and the owned capture to
`POST /calendar/meeting-email/previews`. The existing `CalendarEventCard` displays
the exact action and final confirmation. The first click and preview never call
approve, including when the chat approval menu is set to Always. Editing rejects
the current preview before reopening its fields, so an old payload cannot remain
approvable. Lost preview responses preserve the request key while fields are
unchanged. Existing account/origin-scoped action-reference storage retains only
the action ID and prevents reopening the email from immediately making another
candidate after a queued, uncertain or successful result.

The minimal `CalendarEventCard` change is an optional state callback; normal chat
Calendar behavior is unchanged. This implementation does not modify
`lib/use-assistant.ts`, conversation restoration, `VoiceOrb.tsx`, backend
configuration, authentication or provider accounts.

Verification:

- Full frontend suite: **237 passed**, followed by **5/5** focused component tests
  after adding the stored-status reopening regression.
- Typecheck and local extension build passed.
- Offline Chromium test passed: real extension → synthetic backend, explicit
  typed source → required fields → invitation selection → preview → edit → new
  preview → final exact confirmation under Always.
- Unit tests additionally cover unsafe source markup, locked context, absent
  fields, no inferred attendees, lost-response replay and racing edit rejection.

No live Google or paid model calls are used. The first version leaves date/time
entry to the user and supports one-time timed events. A restored proposed preview
can be cancelled and reopened to edit. Already-created events are displayed as
receipts and cannot be edited by this flow. Requires the matching backend change;
there is no migration or setting change.
