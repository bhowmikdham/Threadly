# Second-budget resumption: restoration passes, drafting still fails

**Stopped at the cumulative call cap: 18/18 second-budget attempts used, zero
remaining.** The nine resumed calls all completed at the provider; none throttled
or failed. This is not a clean behavioral acceptance result.

The [.4 resumption](approved-second-budget-resumption.md) ran from
**2026-10-07 13:35:50 to 13:37:25 UTC**, within its newly approved window ending
13:50:50 UTC. It appended nine calls to the existing second ledger, preserving its
original nine receipts/preflights and its old identity sidecar. The exhausted first
ledger is unchanged. No third budget, new runtime correction, publication,
deployment, real Google write, approval or action job occurred.

New usage: **252,426 input / 1,079 output tokens**, estimated **USD 0.31196341
including 10% GST**. Cumulative second-budget usage: **496,380 input / 2,138 output**,
estimated **USD 0.61355470 including GST**. USD 0.38644530 remains below the money
cap, but this grants no additional attempts. Costs are calculated estimates, not
an invoice. Prices were reverified against the official feed before activation.

## Actual outcomes

| Case | Resumed calls (cumulative IDs) | Outcome |
| --- | --- | --- |
| Independent drafts | 6 (10–15) | **Fail.** The very first request, “Another email for Casey, please.”, entered a schema/repair loop. No Casey goal was created; the later Alex/Casey completion turns were not reached. Original Alex remained the sole pending draft at chat version 1. |
| Saved reply restoration | 2 (16–17) | **Pass for restoration.** The model selected the saved task, then called `review_email_draft(presentation="open")`. The response returned the same task, artifact ID and content, set focus to `saved_task`, and retained the unfinished Focus Calendar goal. No regeneration or permission prerequisite. |
| Calendar | 1 (18) | **Initial preview passes; remainder incomplete.** Quiet hour was proposed for tomorrow at 14:00 Melbourne with the existing 30-minute default and separate approval. Time revision and closing were not attempted because the shared call cap was exhausted. |

The plan targeted 4/2/3 calls. The prioritized goal case needed six before its case
cap stopped it, leaving two for restoration and one for Calendar. These allocation
and global caps were recorded before execution. The harness's `1 passed` means it
recorded execution/isolation successfully, not that all behaviors passed.

## Proven drafting cause

Call 10 at **13:35:50 UTC**, request
`143380f8-7d33-46aa-a8e8-7e5307a1e00f`, asked to start Casey's email with
`continue_previous=false` **and Alex's existing `goal_id`**. The new schema correctly
rejects this combination: an existing ID is valid only for continuation.

The engine's generic `email_draft_invalid_input` repair then instructed the model
to set `continue_previous=true` for the retained goal and exposed Alex's pending
fields. That instruction failed to distinguish a new goal from a continuation.
Call 11 complied with it, but runtime preparation correctly rejected continuing
Alex for the explicitly new email. Subsequent repairs repeated the same generic
continuation instruction. The model tried an empty draft, explicitly selected Alex,
and repeated continuation calls. After six paid responses, the harness rejected
another dispatch and the API returned `conversation_unavailable` (503).

The exact repair messages and requests are in the compressed cumulative ledger.
Relevant branches at unchanged application runtime `398c4ac`:
`PrepareEmailDraft.cited_fields` in `backend/app/schemas/conversation.py`, the
independent-request check in `backend/app/conversation/email_draft.py`, and the
generic `prepare_email_draft` validation-repair message in
`backend/app/conversation/engine.py` near line 646.

This demonstrates a **new-goal contract/repair failure**, not deletion, a provider
outage or a proven repeat of the former goal overwrite. The final synthetic DB
contains only Alex, with empty purpose and original goal ID
`519ec1e0-38c9-4d6c-b9aa-5f1853da4d77`; chat version remains 1. The full original
two-draft switching sequence was never reached, so its live acceptance is still open.

Targeted next correction: distinguish start versus continuation structurally and
make schema-error repair preserve the current operation. For a new goal carrying
an old ID, repair must remove the continuation-only identity rather than force
continuation. Keep the owned-goal guard. Replay this exact first call and subsequent
repair chain before evaluating broader wording. This is a recommendation only;
no such runtime change or further paid test was made in this diagnostic.

## Evidence and limits

- [Machine review](second-resumed-model-review.json): all request IDs, UTC times,
  usage, costs, checks and cumulative totals.
- [Full synthetic ledger](second-resumed-model-ledger.json.gz) and
  [scenario state/responses](second-resumed-scenarios.json.gz), with scripted seeds
  distinguished from actual model decisions.
- [Pricing/auth/full-token preflights](second-resumption-preflights.json) and
  [final failed-draft state](second-resumption-final-state.json).
- Guard harness `669400dd47b13b8650dd827a1857189d3c9cb776`; 21 offline guard/fixture
  checks passed. Application runtime stayed at `398c4acf8de4e878c512836ed8340c133bfe5c13`,
  whose 2,284 backend tests passed before this run. Frontend stayed at `8752c3a1`.

The restored draft was generated in disclosed scripted setup. Its live pass does
not establish end-to-end source/generation quality or held-out language robustness.
Calendar revision/closing and two-draft completion have no passing `.4` live result.
No more paid calls are authorized by the exhausted second allocation.
