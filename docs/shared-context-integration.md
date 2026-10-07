# Shared context and PR122/126/127 integration — unpublished

Runtime/test head: `f4601ff85461283ffe535a847450a0f1427ffd58`.
Original voice base: `afa2180fa16b4060587002b6be440ce32573c603`.
Integrated upstream commits: PR122 `60a7aa8dd02dd94105b90ae1ee68f742cf23d204`
PR126 `1221a2e3bd1b3fe4316ba3f8c4c9e58811243ea1`, and PR127
`e2200f134ddb4a2b772913019291d4afea477874`. Both local merges were clean. No release branch push, publication or deployment occurred.

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

PR127 adds only the upstream three-line `hasChoices` condition, retained exactly.
A ready Calendar action now lifts the orb so the card is accessible during voice.
The existing browser test now verifies a centered clarification, a docked ready
card whose Create event control receives a trial click (no approval), and a centered
social follow-up. It failed before PR127 because the ready card never docked. It
also retains the voice-session, current-callback and speech-fallback assertions.

Verification at the runtime/test head: **254 unit tests passed**, TypeScript and
both builds passed, and **12 focused browser tests passed** (53.0 seconds), covering
voice Calendar conversations, audio lifecycle and meeting-email approval isolation.
The separate public-origin browser case also passed. Public checks use synthetic `https://api.threadly.example.test` with
mocked permission denial. No real service call or release package is produced.

The earlier PR126 head `277be033ee5d7276517adde1ab9acd1886810602` passed all 50 local
browser tests plus its separate public-origin case; that full-browser result is
historical, not claimed as a full rerun on PR127. New evidence is in the owning
workspace's `evidence/chat-context-pr127-*` logs, hashed in the backend receipt.

## Upstream scope

A fresh fetch confirmed `origin/frontend` at
`e2200f134ddb4a2b772913019291d4afea477874`. Its PR127 parent is
`afd93bd507bc5ec3e461f7ddd0bcc54b43063bf2`; the merge commit timestamp is
2026-10-07 13:43:44 UTC. PR127 was initially excluded under the earlier scope,
then explicitly authorized for local reconciliation and integrated here.

- PR125 head `89c41a12cbbe4f1f25fcddc7b40ddbaa62812e22` remains open/unmerged.
  Its `lib/spoken-reply.ts` and `sidepanel.tsx` manual-approval voice message changes
  are excluded; current speech wording and Ask/Always behavior are preserved.
- PR128 head `cac8dae789ff5c0ddc8aadddcf976fa969dc365c` is closed/unmerged and excluded.
- `components/VoiceOrb.tsx` and the PR126 badge files remain byte-identical to
  their released/integrated upstream versions. No broader voice change was made.

The paired backend `.5` operation correction is committed at
`7fb055f409c46479bc1cd1194aa870ff93ccf187`. Its full-suite result is recorded in
the backend receipt. The `.4` live evaluation exhausted the second 18-call allowance:
saved-artifact restoration and initial Calendar preview passed, new-email repair
looped, and Calendar revision/closing were not reached. `.5` has no live model
evidence. Both prior paid allowances have zero calls remaining. Coordinated
API/worker migration and separate release authorization remain required.
