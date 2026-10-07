# Shared context and PR122 integration — unpublished

Runtime/test head: `4e4088b929ebd50184d128d1e63e0c8c07d9b2c3`.
Original voice base: `afa2180fa16b4060587002b6be440ce32573c603`.
New upstream base integrated: `60a7aa8dd02dd94105b90ae1ee68f742cf23d204` (PR122),
six commits after the voice base. Local merge: `d7ab1e05bd80a96ce20756196d951532e855832d`.
No release branch push, publication or deployment occurred.

PR122's `sidepanel.tsx`, `lib/context.ts` and `VoiceOrb.tsx` are unchanged at this
head. Its open-email following, different-email/New chat note, reply-card/insertion
changes and newest visible incoming reply target remain intact. `lib/use-assistant.ts`
also retains the negotiated backend-focus protocol: `context_memory_version: 1`
stops forwarding a displayed task or unchanged source pin as the next turn's focus.
Explicit attach/detach, exact retries and older-backend behavior remain supported.

The controller accepts an existing task/artifact card after a Calendar detour,
loads the saved artifact once and neither restarts generation nor approves actions.
An integrated regression follows two different emails before chat, selects the
newest visible incoming message (excluding hidden newer mail and the user's own
messages), captures it, and then observes a different open email. The chat keeps
its original source and shows the note; New chat captures the newly open email
under a fresh conversation ID. The intervening turn preserves server-owned focus.

Verification: 250 unit tests, 81 focused controller/context/source tests, TypeScript,
local and public-origin builds passed. Full browser run: 47 passed, one public-origin
case skipped and then passed separately against its required build. The public
build used synthetic `https://api.threadly.example.test` with mocked permission
denial; it is not a release package. The paired backend's mechanical receipt records
SHA256 hashes of logs. Browser checks use
synthetic mail and mock backend data; the teammate's separate real-Gmail report
is not counted as our verification.

Paired backend runtime/test head:
`398c4acf8de4e878c512836ed8340c133bfe5c13`, prompt
`contextual-conversation-1.8.9+chat-context.4`, full backend 2,284 passed with no
skips. The backend prevents unbound multi-draft mutation, restores current owned
artifacts, and grounds recognized Calendar control guidance in current action state.
Live-model verification of `.4` remains outstanding. The expired second diagnostic
remains at nine calls and USD 0.30159129 including its 10% GST allowance; unused
calls were not resumed. Coordinated API/worker migration and separate release
authorization are still required.
