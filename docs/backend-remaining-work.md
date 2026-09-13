# Remaining backend work after draft revisions/review

13 September 2026. This is a source-code and automated-test checkpoint, not a
production deployment report. PR #8 is merged; the revision/review slice is on
`codex/draft-revisions-review`. Refer to the [implementation evidence](implementation-playbook/13-implementation-progress.md)
and [24 work packages](implementation-playbook/12-work-packages.md) for scope.

## Available in the current code

- Bedrock Converse adapter behind explicit provider configuration; legacy adapter retained.
- Five-intent proposal routing and backend-controlled durable dispatch.
- Authenticated saved context, task/job persistence, event replay, retry leases,
  cancellation and owner-scoped task history.
- Gmail sync fidelity, source ordering, reply metadata and stale-cache fencing.
- Source-linked summary generation and initial reply/new-email draft generation.
- This branch: explicit draft edits, immutable revision envelopes, history,
  concurrency checks and exact-revision review acknowledgements.

Only summary, reply and compose have generation workflows. Classification into a
category does not install its execution path. Review is not send authorization.
No deployed Bedrock Flow, live send or Calendar execution has been verified.

## Work order and completion gates

| Order | Backend deliverable | Dependency and concrete completion gate |
|---|---|---|
| 1 — current | Shared editor/revision review (T10 partial) | Save full text/envelope revisions, reject stale saves, preserve old copies, invalidate effective review. This branch provides DB/API tests; frontend editor integration remains. |
| 2 | Email action proposal and approval (T05/T10/T12) | Build exact outgoing envelope/MIME/reply header payload from a saved revision; pin sender, recipients, attachments policy, hash and expiry. Explicit Send approval must be separate from Mark reviewed. Edits invalidate executable approvals. |
| 3 | Gmail executor and reconciliation (T12) | Verify granted scope/sender, recheck source, use durable action/outbox states, record provider result, and reconcile uncertain writes before retry. Test timeouts before/after acceptance and duplicate clicks with test accounts. |
| 4 | Context selection and clarification continuation (T06/T17) | Bind UI selections/ordinal references to an authorized captured view. Persist questions and safely resume the intended task without losing context or rerouting an answer as a fresh instruction. |
| 5 | Bounded lookup / other assistance (T09) | Owner-filtered search/entities/commitments/transform/help, evidence coverage and honest no-result behavior; implement supported compound lookup→draft paths. |
| 6 | Non-calendar planning (T14) | Editable plans with dependencies, explicit/inferred/confirmed distinctions, user-selected commitments and linked draft creation. No invented dates or assignees become confirmed facts. |
| 7 | Google capabilities and Calendar reads (T03/T15) | Track actual granted scopes, incremental consent, selected calendars, timezone, work hours/buffers; implement freebusy and deterministic slots. DST and partial-access failures must return correct/unknown results. |
| 8 | Scheduling proposals and Calendar writes (T16) | Bind “tomorrow at 4” to explicit timezone/duration assumptions; return actual slot IDs, recheck chosen slot before a separately approved event create; handle stale slots, notifications and uncertain writes. |
| In parallel with integration | Bedrock Flow runtime (T07) | Version-controlled flow definitions, pinned aliases/releases, IAM/read-tool boundaries, invocation/error adapter, deployment configuration and test-account AWS smoke tests. Current worker uses native bounded orchestration with Converse. |
| Before release | Quality and operations (T18/T19) | Live model evals across five intents/compound requests, Google integration tests, UI end-to-end tests, load/recovery drills, redacted metrics, retention/deletion policy, runbooks, deployment gates and rollout flags. |

This order completes the email lifecycle first. Calendar reads can be developed
independently once capability/scope contracts are agreed. Calendar writes reuse
explicit action approval/execution infrastructure; they must not inherit Gmail's
retry assumptions. Keep one reviewable branch per bounded slice.

## Frontend / AI team dependencies

The frontend needs the latest artifact/envelope, full-edit save, conflict handling,
history, visible blockers, explicit Mark reviewed, and eventually a separate Send
approval UI. It must keep task events active for edits after generation completes.
See [the editor contract and diagram](assistant-draft-review.md).

The AI team still needs live evaluation evidence for intent accuracy, grounding,
missing facts, promises, tone, prompt injection, scheduling extraction and compound
work. Current synthetic fixtures verify output contracts and control flow. They
do not establish writing quality or superiority over Superhuman Go.

Before a usable five-intent demo, integrate the remaining lookup, planning and
Calendar-read paths with the UI and test the original examples end to end:
“summarise this,” “what's in the third message,” “reply,” “write an email,”
“are you free tomorrow at 4,” and “give me three meeting slots.” A read-only slot
proposal can be demonstrated before external sending/event creation is enabled,
but those are different completion milestones and must be labelled clearly.

## Proactive triggers

Opt-in incoming-mail suggestions and follow-up nudges (T20) remain planned.
Current assistant execution starts from an explicit authenticated user request.
A future event/watch service must deduplicate inbound events, check consent,
budget and quiet hours, generate a suggestion without authorizing a write, and
persist dismiss/snooze feedback. It should reuse task contracts rather than bypass
review/approval. Gmail push/watch renewal and background sync need integration work.

## Follow-on scope

Style personalization/RAG (T21), voice (T22), attachments (T23), richer Calendar
changes/recurrence/resources (T24), aliases/contact resolution and large-mailbox
background sync remain additional work. Some are dependencies for richer product
promises; the initial draft path supports literal recipients and plain text only.

There is no defensible overall completion percentage yet: these packages differ
substantially in size and live integration remains unmeasured. Progress bars in
implementation updates track the current slice's milestones only.
