# Combined voice/reply and security-recognition review

This local review branch combines frontend base `4fe4f32` with the voice change
`93eb31f` and security change `ca491fe`. They were cherry-picked into a fresh
worktree; the source branches and shared checkout were not modified. The runtime
commits here are `d8cc7df` and `172738e`. The companion backend combines released
maintenance base `1cb7328` with both corresponding backend changes.

## Interaction review

- Reply draft visibility follows assistant entries and the voice session. Security
  recognition uses its separate protected HMAC store, account/server-scoped cache
  and inbox renderer; neither path changes the other's state or source selection.
- Both preserve authenticated mailbox binding. A selected outgoing message cannot
  auto-fill the owner as the reply recipient. Recognition never alters recipients,
  reply targets, server labels or approval state.
- Draft cards remain usable while voice speaks, including after interruption,
  repeated turns and reopening. Recognition removes only a valid backend-supplied
  security priority contribution after confirmed local acknowledgment; missing data
  keeps the original badge, and independent High/Medium remains supported.
- Existing speech failure/session, source ownership/freshness, row reuse, delayed
  responses, account changes, expiry and storage-failure regressions pass together.
- The only backend file touched by both original changes was API documentation.
  Both additive contract sections survived without conflict. No frontend source
  files overlapped and no combined runtime correction was needed.

## Combined verification

From this worktree:

```sh
npm run typecheck
npm test -- --reporter=dot
npm run build
npm run test:e2e -- tests/e2e/voice-reply-draft.spec.ts \
  tests/e2e/voice-lifecycle.spec.ts tests/e2e/voice-calendar-session.spec.ts \
  tests/e2e/security-awareness.spec.ts tests/e2e/inbox-row-layout.spec.ts \
  tests/e2e/gmail-draft.spec.ts
```

- TypeScript passed. **336 tests passed across 29 files.**
- Production build passed; it prints the existing optional missing-svgo warning.
- **20 packaged browser tests passed** with one worker, disposable headless Chromium
  profiles and local synthetic API/speech/Gmail fixtures. This run includes the final
  late-draft/reopen and new-chat controller source, alongside security recognition
  and the 320px draft-card consent/edit checks. All contexts closed in teardown.
- The combined artifact-draft screenshot was visually inspected at 390px: the orb
  docks above the editable card and owner/correspondent direction is retained.

No shared interactive browser, real account/provider, paid model, real email action,
publication or deployment was used. Fixture draft-save/consent requests are local
synthetic test traffic. Backend prompt 1.0.2 still needs held-out model acceptance and
versioned managed prompt/Flow provisioning before rollout; the reply prompt also has
no new paid model-quality measurement. The unavailable original incident screenshot
was not substituted with these synthetic QA screenshots.
