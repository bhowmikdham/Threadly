# Approved resumption of the existing second budget

Approval: user “yep”, message `Sentinel_06ff4ac627b48191bc8af71e1651a557`,
2026-10-07 13:24:28 UTC, explicitly responding to resuming the remaining nine calls
in a fresh 15-minute window within the same total US$1 budget. The root approval
had specified calls/cost, not a user-imposed 15-minute expiry. The previous cutoff
was a harness safeguard, not an AWS credit expiry. This explicit approval renews
time only; it creates no third allocation and does not reset calls or cost.

Application runtime remains backend `398c4acf8de4e878c512836ed8340c133bfe5c13`
(clean evidence head `3c90a9f807acb3ac2b2378779e3209a88bd56157`), prompt/tools `.4`;
frontend `8752c3a1d67c76bd616638c906ba1cd3bd7f91e2`. Only diagnostic harness,
offline guard tests and evidence are added for this execution.

The existing second ledger's nine calls and USD 0.30159129 estimated cost including
10% GST remain intact. Its raw SHA256 before resumption is
`63efbe33d018c1cdaa9f3c9c6b79d417e67a2197954a5e0ef08c56e62b9d6393`.
The original ledger snapshot and identity sidecar are retained. New entries append
to that ledger and carry the new segment ID. Cumulative limits remain 18 paid
attempts and USD 1 including the GST allowance; failures/unknown outcomes reserve
their full cost and stop further dispatch. All adapters share the guard, with no
SDK automatic retries. Every call is CountTokens-checked at 32,000 input / 1,800
output tokens maximum. No changed model, region, inference tier, caching or Flows.

Same approved AU Haiku 4.5 profile via Sydney `ap-southeast-2`. Only synthetic
conversations plus application prompts/tool schemas are sent to CountTokens and
inference. Google reads are mocked, unexpected HTTP and provider writes rejected;
no action worker, approvals, publication, migration or deployment runs.

Pricing and AWS identity were rechecked at 13:28:40 UTC. The official feed still
lists USD 1.10/M input and 5.50/M output. Nine maximum-sized new attempts cost
USD 0.44649 including 10% GST; cumulative maximum USD 0.74808129. Full first-request
CountTokens and credential preparation finish before the new persisted window
starts. No new call starts after start + 900 seconds; restarts cannot renew it.

Priority and planned calls: independent Alex/Casey goal identity (4), saved reply
artifact restoration (2), Calendar preview/revision/closing (3). Goal selection
repairs may consume up to six calls, prioritizing that case over later Calendar
coverage; the nine-call shared remainder always wins. Saved restoration is capped
at two and Calendar at three, further limited by remaining global allowance.
No held-out extra scenarios. Caps produce incomplete results, never a pass.

Scripted setup uses a new disposable local DB `threadly_context_eval2_resume_test`
on port 55439, preserving both prior diagnostic DBs. It has AWS dispatch disabled.
The three original prompts and disclosed seeds are reused; the model receives no
prescribed tool calls or expected answers. Full state/response evidence and a human
semantic review distinguish isolation assertions from behavioral acceptance.

Offline guard checks: 21 passed, including cumulative limits, preserved receipts,
restart deadline, failed-attempt reservation and all prior budget/fixture checks.
No live calls occurred in those tests. The separate third-budget proposal is
superseded by this resumption and remains unexecuted.
