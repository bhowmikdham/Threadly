# Natural voice conversation candidate

Final local PostgreSQL acceptance supersedes the interrupted-run limitation below:
the exact runtime at `bc81b1d63c166b0488c8ca7fb66d319eb2b19489` passed **2,504 tests,
zero skips**, in484.78seconds after Docker recovered. Both disposable containers
are confirmed absent, including the formerly uncertain container. The interrupted
and offline results below remain historical evidence. Production/model acceptance
is still required; see [release gates](../../release/voice-security-conversation.md).

Release: `contextual-conversation-1.8.11+natural-voice.1`, based on the locally
combined reply/security candidate. The JSON alongside this file freezes the prompt,
tool configuration, social-policy identity and reply hash. Existing historical
assets and live-model evaluation records remain unchanged.

The reported “Let me check that” came from the frontend's unconditional voice
acknowledgment. Repeated self-introduction came from the ordinary conversational
model response. The client now delays selective progress cues; the server handles
bounded complete English greeting, wellbeing, gratitude, closing and identity turns
without model inference or tools. It varies replies from the retained recent chat,
uses reciprocal context for “I'm good, thanks,” and introduces Threadly only on an
explicit identity question. It does not invent human feelings or experiences.

This social handler does not mutate goals, source references or permissions. A
pending content question bypasses the handler because “Hello” or “Thanks” may be a
message body or event title. Compound requests, quotations, non-English work,
corrections, cancellations and ambiguous responses continue through the existing
context-aware engine. Cancel is not relabeled as a successful cancellation. The
ordinary prompt also asks for brief natural replies, relevant dialogue retention,
and truthful final results rather than filler. This is a bounded improvement;
unrecognized semantic phrasing still depends on model interpretation.

Guidance: [Google acknowledgements](https://developers.google.com/assistant/conversation-design/acknowledgements)
and [OpenAI voice prompting](https://developers.openai.com/api/docs/guides/voice-prompting).

## Actual verification

- Ruff and diff checks pass. The current full offline suite passed **1,514 tests**;
  **990 database tests skipped** because PostgreSQL was deliberately unavailable.
- The focused social/provider suite passed **31 tests** after reconnection. The
  release parity test checks the frozen prompt/tool/policy asset.
- Before a Mac connection loss, the new real-PostgreSQL social-detour test passed:
  greeting, reciprocal response and a tool request retain one conversation and
  pinned source. The focused database run also found three existing test assumptions
  that greetings call a fake model. Those tests now use a substantive request where
  they need to inspect model context, and compare stored text with the actual reply.
- The ensuing full PostgreSQL run reached 63% plus 57 cases without reported
  failures, but lost its executor connection before a final result. No test process
  survived recovery. **This is an interrupted run, not a full database pass.**
  Docker is unavailable after recovery, so the final three assertion corrections
  have not been verified with PostgreSQL. The earlier combined base separately
  passed 2,482 tests with zero skips; that does not certify this release's full DB
  suite. The final offline count includes the assertion changes.
- Companion frontend: **349 unit and 22 packaged browser tests passed**;
  typecheck and production build pass. All adapters use synthetic evidence.

The disposable DB container was `threadly-voice-style-review-20261028-1703`, ID
`83b82fe32ec851fa8bd6805bad70f592986e98877de405b510225e5ac4044d8c`, PostgreSQL16,
tmpfs data, no mounts, loopback port50434, `--rm`. The daemon became unreachable
after the Mac interruption, so its removal is **unconfirmed**. No unrelated
container or shared service was restarted or removed.

No paid inference, model swap, real provider read/write, deployment, prompt/Flow
provisioning or publication was performed. Model-quality acceptance remains a
separate rollout gate; these deterministic results do not certify all social wording.
