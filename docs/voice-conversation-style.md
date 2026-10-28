# Voice conversation style

The previous `VoiceOrb` immediately spoke the hardcoded phrase “Let me check that.”
before nearly every request. Its greeting filter stripped “hey” from “Hey Threadly,
how are you?” and then failed to recognize the rest. The subsequent introduction
came from the conversational response, not from that fixed phrase.

The orb now submits the exact utterance immediately with the current chat callback.
A fast answer speaks directly. A still-pending work request gets at most one short
progress cue after 2.5 seconds. The cues alternate within the voice session and
describe pending work without claiming a read, save, send or completed action.
Bounded social, confirmation, correction and cancellation wording suppresses this
presentation cue; it never changes routing or authorizes an action. Other languages
and compound requests continue to the backend unchanged.

A ready response interrupts even a hung progress-speech request. Close, unmount and
barge-in clear progress timers and suppress stale speech; browser cancellation also
settles its utterance and rhythm timer when no end event arrives. Late callbacks
cannot restart the old rhythm or zero the current session's level. Closing voice or
interrupting its audio does not assert that backend work was cancelled: a pending
request may still finish in chat. Spoken cancellation goes through normal task
controls. Draft cards continue to reveal independently of speech completion.

The companion backend release `contextual-conversation-1.8.11+natural-voice.1`
answers a bounded set of complete social turns without inference, retains dialogue
and pinned references, and instructs the ordinary model to avoid repeated onboarding.
Ambiguous short answers to pending content questions retain normal context handling.
Neither side changes model selection, source access or exact-payload approval.

Guidance: [Google acknowledgements](https://developers.google.com/assistant/conversation-design/acknowledgements)
and [OpenAI voice prompting](https://developers.openai.com/api/docs/guides/voice-prompting).
These informed selective, varied, truthful preambles and brief direct social answers.

## Verification

TypeScript, production build and **349 unit tests in 30 files passed**. The combined
packaged Playwright run passed **22 tests**, covering voice reply visibility,
native audio lifecycle, chat/session continuity, security recognition, inbox layout
and draft consent/edit controls. New cases cover named greetings, reciprocal social
follow-up, cancellation, fast tool responses, slow work, a hung cue provider, browser
cancellation without an end event, late callbacks, close and rapid reopen. Browser
profiles were synthetic, disposable and closed. The build retains its optional
missing-svgo warning. No real provider call or external write was used.

Commands:

```sh
npm run typecheck
npm test -- --reporter=dot
npm run build
npm run test:e2e -- tests/e2e/voice-reply-draft.spec.ts \
  tests/e2e/voice-lifecycle.spec.ts tests/e2e/voice-calendar-session.spec.ts \
  tests/e2e/security-awareness.spec.ts tests/e2e/inbox-row-layout.spec.ts \
  tests/e2e/gmail-draft.spec.ts
```
