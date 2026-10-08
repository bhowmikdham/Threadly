# Same-turn goal state repair (.6)

Offline repair of the [.5 diagnostic failure](context5-results.md). This is not a
new live evaluation, release, deployment or claim of conversational acceptance.
Prompt/tool bytes are unchanged; release metadata and the asset snapshot advance
to `contextual-conversation-1.8.9+chat-context.6` for the runtime change.

## Proven cause and scope

On 2026-10-08, call `3d094519-4646-4412-b502-0bf8bcfc07c4` validated Alex's purpose
but requested generated draft text. Repair call
`5a251cde-0c82-4d91-8f82-33e50c9af279` supplied the draft and omitted the already
retained purpose. `bind_email_continuation` called `select_goal` again, reloading
the old empty purpose from PostgreSQL over the validated current-turn value.
The first new-Casey call `21f4b699-7c92-4a47-b9a2-e81cea3eb069` had succeeded.
The exact three recorded responses now run in default test discovery; only the
recorded masked goal identity is rebound to the synthetic fixture's owned goal.

The initial expanded baseline had **9 failures / 4 passes**. It exposed the
shared selector overwrite across Calendar, email draft, reply and mail search,
same-turn cancelled-goal resurrection, new-email/new-Calendar field retry loss,
and loss of a validated email correction when generated text was missing.
An earlier baseline had a test-fixture indexing error, corrected before this run.

## Canonical ownership and update rules

1. PostgreSQL owns committed independent goals, chat ownership/account fences,
   version/lease authority, closed status, tasks, artifacts and actions.
2. During one claimed turn, the typed state slots hold the latest **validated**
   working value for each focused goal kind. Tool inputs are not copied into
   those slots before their existing provenance/operation checks.
3. `_goal_updates` is an internal, bounded-by-tool-calls write set of full typed
   values keyed by server-owned goal identity. It captures outgoing goals before
   replacement and validated partial progress even when generation needs repair.
   It is neither model input nor a serialized chat cache. A new uncommitted goal
   receives a server ID before switching away so completion can retain it.
4. Every selection rechecks the current USER source, owned chat, database row,
   expected kind and both durable and current-turn cancellation. Only then can
   the latest working value replace the older committed payload. An unknown
   model-supplied ID is still rejected; staging never bypasses the row lookup.
5. Checkpoint/completion checks the lease before saving **all** staged goals in
   the same transaction. Unchanged hashes keep their version. The write set is
   removed before compaction/encryption. A failed lease writes no staged fields.
   Exceptions without a checkpoint do not make uncommitted work durable.
6. Retention is monotonic in validated information, not a union of nonempty
   strings: explicit replacements/clears/removals supersede earlier fields.
   A new independent USER request resets its own fields; cancellation closes
   only its target. Neither reselection nor generated text may undo these changes.
7. Reviewed Calendar actions remain immutable until a complete validated revision
   retires them. Invalid amendment bundles preserve the reviewable candidate;
   independently grounded partial fields may accumulate in an unreviewed goal.
   Corrected text-email fields remain in clarification until fresh valid draft
   text exists; the prior text is not presented for the corrected recipient.
   If only a requested wording revision fails and the validated recipient,
   purpose, envelope and cited context are unchanged, the previous reviewable
   draft and its save receipt remain available. Its identity does not change
   until new generated text passes validation.

## Reload and merge audit

| Path | State rule / disposition |
| --- | --- |
| Email `bind_email_continuation` → shared selector | Same ID/source/owner checks on every call; latest validated turn value survives repeated repair. |
| Generic `select_conversation_goal` | Same fix covers all four typed kinds, repeated selection, switching away/back and commit while another goal has focus. Task/proposal/artifact ownership and fresh status reads remain unchanged. |
| Goal `persist`, claim, checkpoint, completion | Registry is the cross-turn store. All changed working goals flush under the existing lease; staging is never serialized. Kind cannot change under an existing ID. |
| Goal listing | Read-only committed metadata with stable IDs and existing pagination; it does not reload or overwrite working payloads. Labels may reflect the last commit until the turn completes. |
| New email retry | Same request ID plus a runtime marker from validated text preparation allows omitted fields to be retained after generation failure. An unrelated older goal cannot satisfy this condition. |
| Email continuation/correction | Current USER and citation checks still validate changed fields. Valid corrections now persist before text validation; old recipient envelopes are not inherited. Failed wording-only revisions retain the existing reviewable draft/save receipt. |
| New Calendar retry / `event_draft.merge` | Only this request's unreviewed partial creation can be reused by a repeated create call. Original source/date anchors persist. Joined repair evidence cannot form a title pattern across separate source lines. |
| Calendar amendment / `retain_partial` | Existing typed changes and all-or-nothing reviewed-candidate revision remain. Reselection cannot restore a cleared date or superseded action ID. |
| Calendar choices and candidate response | Expiry, account/preference versions, eligible destination reads and current owned action status remain checked by the original paths. No provider observation becomes reusable authority. |
| Mail search/reply merge | Validated state assignments participate in the same write set; explicit new-search/reset and reply cancellation remain distinct. Reply source identities are restored as handles, not bodies or read authority. Fresh source-scope checks remain. |
| Batched mail-read rollback | Whole-state rollback remains atomic, including transient goal staging. Failed provider reads do not become remembered facts. |
| Restore, saved task/artifact resumption, draft compaction | Cross-turn reads still use committed owned records/current artifacts. Reselection can hydrate omitted generated text from the registry only for a still-drafted value with the matching draft ID; it cannot restore an earlier purpose or recipient, or text for a newer ID. |

## Verification and limits

The exact live-response replay, four-kind switching and lease checks, ownership,
source/kind/chat rejection, durable/local cancellation, new-goal checkpoint,
email generation/correction and Calendar repair regressions run in
`backend/tests/test_goal_turn_state.py`. The final focused run passed **185 tests**.
The first full run found **4 failures / 2,317 passes**: failed wording-only
revisions had incorrectly invalidated the reviewable draft and its save receipt.
Those existing regressions now pass with conditional draft retention. A separate
compacted-text restoration edge case was reproduced RED and fixed with exact
draft-ID hydration. Final full-suite results and hashes are recorded in the
accompanying mechanical receipt. No failing tests were removed or weakened.

Final backend gate: **2,322 passed, no skips**, one existing Starlette deprecation
warning, in **290.80 seconds**. Ruff and changed-file formatting checks passed.
The final focused set passed **185 tests** in **32.02 seconds**.

All model decisions are recorded or scripted; Google adapters are fake; PostgreSQL
is the disposable local `threadly_meeting_email_test` database. The three diagnostic
databases and original ledgers are not test targets. Frontend and released voice
files are unchanged. This evidence establishes the reproduced runtime correction,
not model goal-selection quality or a successful real Google write.

## Concrete live-resume proposal (not executed or authorized)

Keep the third ledger at **3/12 attempts, USD 0.10397772 including GST**, with
**9 unused attempts: 3 email, 6 Calendar**. The stop-on-regression rule was triggered;
the prior 15-minute window expired. Neither a new budget nor a new window is implied.

If explicitly authorized, first pin this repair's exact commit/asset and renew
read-only auth/pricing/full-input token preflights. Append a resumption segment
to the same ledger, preserving all three receipts, its identity and cumulative
caps. A new 15-minute window starts only after preflights and explicit approval.
Keep the same AU Haiku model, 32k input / 1,800 output ceilings, eight-second
spacing, no SDK retries and the original USD 0.60 incl-GST cumulative ceiling.
Recalculate the conservative remaining reservation if prices change.

Use up to three email attempts first: resume Alex from the preserved pre-failure
synthetic state with the validated purpose supplied by the current USER turn;
complete the generated draft (allowing the same omitted-purpose repair); then
return to Casey and complete that independent goal. Inspect goal hashes and text
after each USER turn. If extra repair attempts consume this allocation, report
incomplete coverage instead of borrowing from Calendar or claiming success.
If both drafts complete in two calls, the remaining email call can cover the
original optional new-Morgan variation. Then use at most six Calendar attempts
for the original four USER turns: “Put Quiet hour in my diary tomorrow at 2 pm.”,
“Make it 3 pm instead.”, “Thanks, that's all.” and “Where do I confirm it?”.
Check preview/revision fields, closing/control guidance and action/approval
isolation after each turn. Stop immediately on any semantic/state regression, cap, timeout or
provider failure. No mail/Calendar writes or production changes are part of this
proposal. End-to-end conversational acceptance remains open until observed.
