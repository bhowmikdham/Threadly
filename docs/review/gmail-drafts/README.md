# Editable Gmail draft card review

Both named-recipient generated text and saved draft artifacts use the same Gmail
card, with editable To/Cc/Bcc, subject and plain-text message. Create draft saves
only the clicked editor snapshot. Saved, pending, permission, rejected and unknown
states stay distinct. The card has no send or approval control.

Synthetic browser previews, inspected at 420px and 320px in light/dark themes:

- [Light editor, 420px](gmail-draft-light-420.png)
- [Light editor, 320px](gmail-draft-light-320.png)
- [Dark editor, 320px](gmail-draft-dark-320.png)
- [Confirmed Gmail draft, 320px](gmail-draft-created-320.png)

The original Library reference image could not be materialized after its supported
retry. These are actual built-extension screenshots, not a claim of matching the
unavailable reference.

Validation of the original card: typecheck and changed-file Prettier check pass; all 227 unit tests,
36 browser tests, and 29 release packaging tests pass. The public-origin browser
test is validated separately with the packaged HTTPS build. Initial full runs
exposed inherited voice acknowledgment assertion drift at base 3469b4e, reproduced
in an untouched baseline checkout. With explicit user approval, a separate test-only
commit updates the expected wording to “Let me check that.” All sequence,
cancellation and stale-audio checks remain; voice runtime code is unchanged.

Provider writes are mocked. Tests cover exact editor values, recipient validation,
Cc/Bcc, keyboard activation, duplicate clicks, saved-result recovery, missing draft
permission, stale-chat disablement, unmount, and long subject/body overflow. Backend
PR supplies verified account/source gates, draft-only Gmail persistence and the
durable deduplication receipt. No live Gmail write, OAuth grant or deployment was
performed. Existing grants must include gmail.compose, gmail.modify or full-mail
access; gmail.send alone does not permit drafts. Initial OAuth still requests
gmail.readonly. No live user's
stored grants were inspected. Missing/unverified permission and disconnected
accounts show explanatory states with Create draft disabled and edit/copy available.

The backend migration g071026e9042 follows c061026e9041 and adds only
gmail_draft_saves. Apply it before enabling this UI. Older application code can
coexist with this additive table. Retain the table on application rollback:
downgrade deliberately refuses to delete any receipt, including uncertain writes.
The UI stays disabled if the paired backend routes/capability are unavailable.
Receipt recovery keeps the original saved source revision distinct from newer
generated edits, which remain editable/copyable without creating duplicate drafts.

The separate consent follow-up adds **Enable draft creation** when the paired
backend advertises gmail_draft as requestable. Before clicking, users see that
Google's compose permission includes managing drafts and sending email, while
Threadly uses this feature only to save drafts. The existing bound OAuth/PKCE flow
preserves editor fields, handles cancellation, refreshes granted capability/account
version, and requires a new Create draft click. It does not enable sending or
automatically create any draft. This follow-up has separate release approval;
no actual Google grant or console/security setting is changed by its tests.

Follow-up verification: 229 unit tests, 37 browser tests and the separately built
HTTPS public-origin test pass, along with typecheck and formatting. The built
[320px consent preview](gmail-draft-consent-320.png) was inspected. Browser tests
mock only Google/provider responses and cover explicit reconnect, cancellation,
account guard, preserved subject/body, refreshed capability and no save until a
new click. Existing voice behavior and assertions are unchanged by this follow-up.
