# Proposed live check of structural corrections — not authorized

Superseded: the user instead approved using the nine remaining calls of the
existing second budget. See [approved resumption](approved-second-budget-resumption.md)
and [actual results](second-resumption-results.md). This additional-budget proposal
was never executed; the second budget is now exhausted.

This is a new bounded diagnostic, not a restart of the expired second batch.
Do not run it until separately approved. First complete the full mechanical gate
for backend `398c4acf8de4e878c512836ed8340c133bfe5c13` and record the final clean
documentation head. Pin prompt/tools to `contextual-conversation-1.8.9+chat-context.4`.
No model, route, region or production configuration change is proposed.

Use the same Sydney endpoint `ap-southeast-2` and inference profile:
`arn:aws:bedrock:ap-southeast-2:710507379899:inference-profile/au.anthropic.claude-haiku-4-5-20251001-v1:0`.
CountTokens uses the verified bare model ID
`anthropic.claude-haiku-4-5-20251001-v1:0`; inference remains on the profile.

## Hard limits

- At most **18 paid attempts**, at most **6 per case**, including all auxiliary
  calls, repairs, workers and failed/throttled attempts. No automatic SDK retries.
- At most **32,000 counted input tokens / 1,800 output tokens per attempt**.
  Oversize or failed CountTokens stops that request before inference.
- At most **USD 1.00 including a 10% GST allowance**. Using the previously verified
  official prices of USD 1.10/M input and 5.50/M output, the full 18-attempt maximum
  is USD 0.81180 before tax / **0.89298 including GST**. Reverify pricing read-only
  before activation; stop if these limits no longer fit. Failed attempts reserve
  their entire maximum cost.
- One persisted **15-minute window**, starting at approved activation; at least
  8 seconds between paid attempts. Persist identity/deadline before the first call.
  Process restarts cannot reset calls, price limits or deadline.
- A new distinct ledger and disposable database; preserve both earlier ledgers and
  diagnostic DBs. All setup uses scripted adapters, with AWS dispatch prohibited.
- Same shared dispatch guard for every model adapter/worker. Fake Google sources
  only, reject provider writes and unexpected operations, no action worker, approvals,
  mail/calendar mutation, publication or production change.

## Cases and pass conditions

1. **Independent drafts:** seed unfinished Alex; ask for Casey, complete Alex,
   then return to Casey using the exact prior wording. Inspect stable IDs and
   Alex's unchanged payload hash. If the original sequence fits within its cap,
   use a held-out recipient-revision phrase and verify that the explicitly intended
   original goal is revised while the other draft remains intact.
2. **Calendar guidance:** repeat the exact preview, time revision and closing turn;
   expect the correct preview and a social acknowledgment. Within the same cap,
   ask a held-out control/status question; the response must reflect the current
   owned action and expose no nonexistent draft control or completed-write claim.
3. **Saved artifact return:** seed the saved agenda reply plus Calendar detour;
   repeat the exact return request. Require the actual unchanged task/artifact card,
   restored focus and retained Calendar details. Use a held-out detour/return phrase
   only within the same six-attempt cap; no regeneration, permission prerequisite,
   approval or provider write may occur.

Do not prescribe tool calls or expected answers in model input. Review full traces,
owned persisted goal/action/task state, UI response payloads, latency and token/cost
receipts. Score each original sequence and held-out variation separately. A cap or
deadline interruption is incomplete, never a pass. Stop on an observed invariant
violation; preserve evidence and fix offline rather than spending the remaining
allowance on repeats. Zero live calls have been made for this proposal.
