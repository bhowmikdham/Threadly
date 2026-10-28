# Voice reply review

Local candidate on frontend `4fe4f32` (0.2.8), isolated from the security cue changes.

Draft cards now request chat space for both durable draft artifacts and conversational
text drafts. Text drafts receive a short spoken review cue rather than having their
entire body read aloud. Chat state reveals newly completed drafts independently of
speech completion, so a pending acknowledgment, interrupted turn, late worker result
or short acknowledgment cannot leave the card covered. A draft pending before voice
reopens can still reveal itself when it completes. Starting another chat ends voice.
The docked overlay no longer marks the rest of the chat as modal/inaccessible.

Reply recipient auto-fill refuses the owner's own outgoing message. The newest
inbound default, explicit selected target, account mismatch checks and reference-only
context capture retain their existing rules.

Verification uses synthetic mail/speech and disposable Chromium profiles. No real
mail, provider inference, Gmail draft creation, send, approval, deployment or PR was
performed. The new draft visibility tests failed before the fix. Both packaged card
types were visually inspected at 390px while the orb was speaking and the editor was
usable; the From/To fields retained owner/correspondent direction.

Commands:

```sh
npm run typecheck
npm test -- --reporter=dot
npm run build
npm run test:e2e -- tests/e2e/voice-reply-draft.spec.ts \
  tests/e2e/voice-lifecycle.spec.ts tests/e2e/voice-calendar-session.spec.ts
```

Browser results: **13 passed** (two draft visibility/repeated-turn cases, two native
audio lifecycle cases and nine existing voice/calendar/session cases). Unit coverage
also tests barge-in, pending acknowledgment, close/reopen, late replies, muted turns,
StrictMode, owner-target auto-fill and ending voice on a new chat. The final unit
count is **297 passed** across 27 files; TypeScript and the final production build
pass. The build warns that optional `svgo` is absent for SVG minification. Browser contexts are closed;
the shared interactive browser was not used. The build and loopback/browser tests
needed ordinary sandbox escalation; automatic review approved them.
