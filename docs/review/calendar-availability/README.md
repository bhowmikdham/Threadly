# Calendar availability card

The single-time availability answer remains visible, followed by a card using
the existing Calendar event card layout. It shows free/busy/uncertain status,
date, time, timezone, selected-calendar scope, and requested or saved duration.
Incomplete checks cannot confirm free time. Expired checks become historical.
Optional details contain checked time and busy intervals, never event titles.

`Check again` starts a new read turn for the latest check in the current
conversation. Older cards cannot refresh a newer request's window. Cards are
not rebuilt from persisted conversation prose. No creation or approval control
is added.

The additive payload requires backend conversation release `1.8.4`. Older
responses continue to display their text without a card.

## Review images

These use synthetic responses through the built extension and mock backend.

- [Busy, light theme, 343px](busy-light.png)
- [Incomplete coverage, dark theme, 320px](partial-dark.png)

## Verification (6 October 2026)

- `npm run typecheck`: passed.
- `npm run build`: passed.
- `npx vitest run tests/calendar-availability.test.tsx`: 6 passed.
- `npm run test:e2e -- --grep 'availability|Calendar approval|clickable calendars'`:
  5 passed, including keyboard recheck, latest-only refresh, narrow light/dark
  rendering, and no event/approval API calls from availability.
- Full `npm test` after the approved test correction: **200 passed**.
- Full development browser suite: **35 passed**, with its public-origin case
  intentionally deferred to a separate public-origin build; that check passed
  **1/1**. Both development and public-origin builds passed.
- Release-tool checks: **29 passed**.
- Initially, full `npm test` returned 192 passed and 8 failed, all in
  `voice-orb.test.tsx` (6) and `voice-audio-lifecycle.test.tsx` (2).
- Replaying those two voice files on unchanged base
  `dd62cf4642c3d69f574138398e52c58c3b2d4ad5` reproduces the same 8 failures
  (11 passed). The new voice acknowledgment/docking behavior differs from its
  existing tests. The follow-up test-only commit explicitly expects acknowledgment
  followed by the answer and docking only for choice replies. Restart tests verify
  cancellation and cleanup, then deliver the old acknowledgment completion and
  old response after reopening and assert no additional speech or stale callbacks.
  The browser voice test also verifies both phrases in order. This PR does not
  change voice runtime behavior.

The base also imports a missing `VoiceReply` type from `VoiceOrb`; this change
exports its existing inline type to restore typechecking without changing the
component's runtime contract. No production deployment or real Calendar write was run.
