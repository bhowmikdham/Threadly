# Structural corrections after the second diagnostic

Candidate: `contextual-conversation-1.8.9+chat-context.4`.
Backend runtime/test commit: `398c4acf8de4e878c512836ed8340c133bfe5c13`.
Initial correction commit: `1bcb9c4fc8bc03e7c9b289e135d1dc5c0e9378fc`.
Frontend integration/test commit: `4e4088b929ebd50184d128d1e63e0c8c07d9b2c3`.
Both are local and unpublished. No production or provider writes occurred.
The final mechanical receipt records checks and source hashes.

## Findings and correction

The [second real-model report](second-model-review.json) records the original
`.3` failures, provider request IDs, usage and exact tool traces from
2026-10-07 12:07:26–12:08:53 UTC. The archived report and ledgers remain immutable.

| Proven failure | Backend correction | Regression evidence |
| --- | --- | --- |
| Call 4 passed Casey text with `continue_previous=true` while Alex remained selected. Alex's goal ID was overwritten; Casey's retained goal stayed unfinished. | `prepare_email_draft` binds a typed, owned `goal_id` with the current USER request, or requires explicit selection in this turn when multiple email goals exist. Unqualified continuation is rejected before mutation; recipient strings never route between goals. | Replay the exact call, reject it, then explicitly select Casey. Assert Alex's payload hash unchanged and both IDs distinct. Direct ID binding and intentional recipient revision also pass. |
| Call 7 responded to “Thanks, that's all.” with “click Create draft” for a Calendar event. | Whole standalone closings return an acknowledgment. Recognized Calendar control guidance reloads the owned action and returns its real card/state; incomplete requests report missing details. | Exact closing replay; bad control advice against proposed, approved, succeeded, failed and outcome-unknown synthetic states. No action jobs. |
| Calls 8–9 read the source and called `review_email_draft`, but returned saving/permission guidance without opening the saved agenda reply. Calendar stayed focused. | Review defaults to opening existing work and returns the owned task/current artifact or text editor. Backend focus is restored; unrelated Calendar state is retained. Status presentation remains explicit. | Exact tool replay returns the same saved artifact/task. Retry returns existing work; other owner rejected; no new worker job. An intervening local edit returns the newer revision and marks the old save receipt as `earlier_revision`. |

## Architectural review

Goal selection checks the owned conversation, retained status and expected email
kind before changing focus. Complete current USER text binds inline selection;
source citations retain their existing validation. A same-turn explicit selection
can be followed by preparation without another lookup call. A single retained
goal keeps legacy continuation compatibility. With several retained goals,
unqualified calls intentionally fail with a repairable contract error. Cancellation
cannot bypass this binding check through the preparation path.

Draft reopening takes the server-loaded task pointer, checks task/artifact ownership,
and reloads the final revision. It does not accept an arbitrary new model artifact
ID, start generation, create a Gmail draft, grant a permission or approve a write.
The frontend already accepts a message carrying a task card: its new controller
regression verifies one artifact fetch and no work restart or approval, including
the next turn's omission of a stale client task pointer.

Calendar guidance uses the existing durable response path. A newer unfinished
request takes precedence over a prior action. External execution remains behind
the existing exact-payload approval boundary. The voice implementation, separate
meeting-email candidate approval, source provenance and coordinated migration
requirements are unchanged.

## Verification boundaries

Four primary regressions failed for the expected behavior before implementation
and passed after it. The first red attempt also exposed a test fixture using the
wrong DB pool; after correcting that fixture all four had genuine behavioral
failures. An expanded run found two old multi-goal fixtures without explicit goal
binding and one synthetic invalid action state; the fixtures were corrected to
match the new contract and real state enum. A stricter cancellation assertion then
exposed an earlier request-source rejection; the fixture now reaches the intended
goal-binding check. An additional targeted-cancellation regression exposed a real
edge case: valid current cancellation text was rejected by the creation validator.
The final correction verifies current-turn provenance without requiring positive
creation intent, and still binds the selected owned goal before closing it.
The cancellation case failed before the fix and then passed with 52 drafting
regressions. These unsuccessful runs remain in the local evidence directory.

Final targeted coverage: 194 backend checks, followed by 17 final invariant checks
including the additional stale-revision case, then 53 checks including cancellation.
Frontend after PR122 integration: 250 unit checks and typecheck, including 81 focused
controller/context/source checks.
The full backend result is in [mechanical-verification.json](mechanical-verification.json).
The initial correction passed 2,283 backend tests before the final cancellation fix.
The final backend at `398c4ac` passed **2,284 tests, no skips**, one Starlette warning.
Reconciled frontend builds passed; full browser run: **47 passed / 1 skipped**,
then the skipped public-origin case passed separately using a synthetic `.test`
origin. No real service request or deployment is implied. The first local build
was blocked by the process sandbox; the same local build passed with escalation.
The receipt records log hashes; the compressed log bundle retains failures too.

## Frontend dependency reconciliation

PR122 was merged upstream during this work. Fetched and verified frontend head
`60a7aa8dd02dd94105b90ae1ee68f742cf23d204`, six commits after the original voice base.
Local merge `d7ab1e05bd80a96ce20756196d951532e855832d` preserved its open-email following,
different-email/New chat note, insertion/reply-card changes and newest visible
incoming-message reply target. `sidepanel.tsx`, `lib/context.ts` and `VoiceOrb.tsx`
remain byte-identical to that upstream head. The merged `lib/use-assistant.ts`
retains both its following behavior and our server-owned focus protocol.

A new integrated controller test follows two emails before chat, captures the newest
visible incoming target (excluding a newer hidden message and the user's own mail),
then opens the first email again. The existing chat keeps its captured source and
shows the different-email note; the next turn omits stale client source/task pointers.
New chat follows the newly open email and captures it under a different conversation.
The teammate's real-Gmail report is separate from our local mocked-browser evidence.

These use real disposable PostgreSQL, scripted model outputs and fake Google
adapters. They prove the recorded bad call cannot silently overwrite another goal
and that the existing draft is surfaced for the recorded review call. They do not
prove a real model chooses the intended goal ID or tool across varied wording.
Calendar guidance recognition is a bounded language check, not universal semantics.
An older non-current draft still requires explicit goal selection. Literal history
retrieval and compact context limits remain as previously documented.

## Budget and next gate

At the correction checkpoint, `.4` had not been sent to Bedrock. The ledger was **9/18 attempts**,
**243,954 input / 1,059 output tokens**, **USD 0.30159129 including 10% GST**.
Its 12:22:26.360372 UTC deadline expired while offline fixes were in progress;
the nine unused attempts remain unused. The first exhausted ledger is also unchanged.

The user subsequently approved a new time window to use those nine remaining
calls within the existing second allocation. The [resumption results](second-resumption-results.md)
show saved restoration passing, a new-goal repair loop failing, and partial Calendar
coverage. The second ledger is now 18/18, with USD 0.61355470 including GST; its
original receipts are preserved. No third allocation was used. Live semantic acceptance,
final release review and separate publication/deployment authorization remain gates.
Migration `h071026e9043` still requires coordinated API/worker rollout; no migration
or deployment was performed here.
