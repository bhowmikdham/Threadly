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

Validation: typecheck and changed-file Prettier check pass; 207 non-voice unit tests,
33 non-voice browser tests, and 29 release packaging tests pass. The public-origin
browser test is validated separately with the packaged HTTPS build. Full unit and
browser runs expose the inherited voice acknowledgment mismatch at base 3469b4e:
source says “Let me check that.” while tests expect “Sure, I can do that.” Seven
unit failures reproduce in an untouched baseline checkout; the full browser suite
has the corresponding one voice failure. Voice source/tests remain unchanged.

Provider writes are mocked. Tests cover exact editor values, recipient validation,
Cc/Bcc, keyboard activation, duplicate clicks, saved-result recovery, missing draft
permission, stale-chat disablement, unmount, and long subject/body overflow. Backend
PR supplies verified account/source gates, draft-only Gmail persistence and the
durable deduplication receipt. No live Gmail write, OAuth grant or deployment was
performed. Existing grants must include gmail.compose, gmail.modify or full-mail
access; gmail.send alone does not permit drafts.
