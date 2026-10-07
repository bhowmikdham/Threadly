# Shared context, PR122 and PR126 integration — unpublished

Runtime/test head: `277be033ee5d7276517adde1ab9acd1886810602`.
Original voice base: `afa2180fa16b4060587002b6be440ce32573c603`.
Integrated upstream commits: PR122 `60a7aa8dd02dd94105b90ae1ee68f742cf23d204`
and PR126 `1221a2e3bd1b3fe4316ba3f8c4c9e58811243ea1`.
The PR126 merge was clean. No release branch push, publication or deployment occurred.

PR126's six changed files are byte-identical to that merge: `background.ts`,
`contents/inbox-badges.ts`, `lib/classification.ts`, `lib/gmail-context.ts`,
`tests/classification.test.ts` and `tests/e2e/inbox-badges.spec.ts`.
Its account-matched classification requests, session cache and two-request
concurrency limit are retained. Badge browser checks exercise rendering, unknown
classification, throttling/cache and account mismatch on synthetic Gmail pages.
They do not contact Gmail or assess real model classification quality.

PR122's open-email following, different-email/New chat note, reply insertion and
newest visible incoming reply target remain intact. The context controller keeps
the negotiated `context_memory_version:1` protocol: it does not forward the last
displayed task or unchanged source pin as the next turn's focus. Explicit pin
changes, exact retries and older-backend behavior remain supported. Restoring a
saved artifact after a Calendar detour does not restart generation or approve it.
The separate meeting-email Create event button still uses its own manual preview
and exact confirmation. `components/VoiceOrb.tsx` is unchanged from the voice base.

Verification at the runtime/test head:

- 254 unit tests passed (24 files); TypeScript passed.
- Local and synthetic public-origin extension builds passed.
- Full browser suite: 50 passed, one public-origin case skipped as expected for
  the local build. That case passed separately against its required public build.
- Public configuration used `https://api.threadly.example.test` with mocked
  permission denial. No real service call or release package was produced.

Logs under the owning workspace's `evidence/chat-context-pr126-*` are hashed in
the paired backend mechanical receipt. These checks include chat source/focus,
meeting-email approval isolation and voice lifecycle behavior.

## Inspected, excluded upstream changes

At inspection, `origin/frontend` was `e2200f134ddb4a2b772913019291d4afea477874`,
which already merged PR127 at 2026-10-07 13:43:44 UTC. Only PR126 was authorized
for adoption in this task, so the later merge was deliberately excluded.

- PR125 head `89c41a12cbbe4f1f25fcddc7b40ddbaa62812e22` changes
  `lib/spoken-reply.ts` and `sidepanel.tsx`. It adds an unconditional manual-approval
  voice message when a Calendar action ID is present; review against actual
  Ask/Always/current-action state is needed before integration.
- PR127 head `afd93bd507bc5ec3e461f7ddd0bcc54b43063bf2` changes
  `lib/spoken-reply.ts` to lift the voice orb for a ready Calendar action.
  It does not change `VoiceOrb.tsx`. Neither PR's patch was adopted or tested here.

The paired backend `.5` operation correction is committed at
`7fb055f409c46479bc1cd1194aa870ff93ccf187`. Its full-suite result is recorded in
the backend receipt. The `.4` live evaluation exhausted the second 18-call allowance:
saved-artifact restoration and initial Calendar preview passed, new-email repair
looped, and Calendar revision/closing were not reached. `.5` has no live model
evidence. Both prior paid allowances have zero calls remaining. Coordinated
API/worker migration and separate release authorization remain required.
