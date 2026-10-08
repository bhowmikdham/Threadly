# Email-card Create event

Integrated local implementation on released frontend base
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

The minimal `CalendarEventCard` change is an optional state callback. This stack
also contains the separate backend-owned focus and draft-restoration changes in
`lib/use-assistant.ts`. The integrated browser regression confirms that the email
button preserves that protocol. `VoiceOrb.tsx` remains unchanged from the released
base. No backend configuration, authentication or provider account changes occur.

Verification:

- Combined full frontend suite: **240 passed**.
- Typecheck and local/public extension builds passed.
- Combined full browser suite: **47 passed / 1 skipped**. The packaged public-origin
  case then passed separately against its required public-origin build.
- The extended meeting-email browser case passed again with the new context
  protocol: after confirmation, a follow-up keeps the chat identity/version and
  omits derived task/pin focus. No second approval occurs.
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

Code/test head: `73d6e491a82105926b93b22be72aef172b1d5e01`, branch
`codex/chat-context-restoration`. Original button commit `fb6d8d5` remains on its
separate branch; integrated equivalent is `53e1770`. Context restoration commit
is `34391a2`. Evidence-only commits follow. Backend approval-isolation regression
and complete source hashes are recorded in the matching backend branch's
`docs/evaluation/chat-context/` directory. No live-model quality claim is made.
