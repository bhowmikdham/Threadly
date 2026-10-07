# Second approved diagnostic: prompt .3

Approved at **2026-10-07 11:45:58 UTC**, user message
`Sentinel_5ad560968be0819184efa9a9963fbad1`, as relayed by the parent. Scope:
**18 additional attempts / USD 1 including applicable GST**, same production
Sydney Haiku 4.5 AU profile, synthetic data plus application prompts/tool schemas
for CountTokens and inference. No real provider writes, publication or deployment.
The exhausted first ledger and its negative outcomes remain immutable.

This is a targeted follow-up, not a claim that the original three cases now pass.
The `.3` prompt/tool snapshot is unchanged during this run. The production runtime
remains at the separately reviewed local repair `744982f`; changes here are harness,
fixtures, tests and evidence only.

| Case | Disclosed scripted setup | Actual model turns | Maximum attempts |
| --- | --- | --- | --- |
| Corrected drafting | One unfinished Alex draft, no generated text | Start Casey independently; finish Alex's sapphire-crate note; finish Casey's map thank-you | 6 |
| Calendar | Empty owned chat, synthetic account/preferences, Ask approval | Prepare Quiet hour tomorrow at 2 pm; revise to 3 pm; acknowledge thanks | 6 |
| Saved-draft restoration | Actual local saved reply artifact created with scripted models and fake Gmail, including recipient clarification; then an unfinished Focus Calendar request | Set that aside and return to the saved agenda reply | 6 |

The scripted setup is excluded from quality scoring and uses no AWS calls. It does
not establish real-model generation/routing quality. The saved-draft case must return
the exact existing task/artifact, preserve the unrelated Calendar goal and create no
new generation job. The Calendar case stops at previews: no approval or action worker
runs. Two completed name-only drafts test text generation and goal identity, not live
Gmail saving or source-based worker generation.

All coordinator, auxiliary and worker calls share the second ledger and caps.
CountTokens uses the verified same-model bare ID, inference the unchanged profile.
Each attempt is bounded to 32,000 counted input and 1,800 output tokens, one SDK
attempt, no caching/guardrails/priority/Flows. At the verified USD 1.10/5.50 per
million input/output prices, the worst-case total is USD 0.8118 before GST or
USD 0.89298 allowing 10% GST. Each new invocation rechecks the persisted 15-minute
batch deadline. Eight-second pauses between attempts reduce bursts following the
first run's throttle; they do not retry failures or change the token/call caps.

A provider/count failure halts paid execution. Calls consume allowance before
dispatch, including failures or interrupted results. A used ledger prevents normal
pytest collection from reseeding the evaluation DB; any recovery must explicitly
resume the saved pending turn with the same identity, budget and remaining time.
No unused first-budget dollars or calls carry over. The first ledger is not opened
for writing by this harness.

Files: `backend/tools/evaluate_chat_context_followup.py` and its offline test
`backend/tests/test_chat_context_followup_evaluation.py`. The isolated second-run
DB is `threadly_context_eval2_test` on the owned local server, port 55439. The second
ledger is `evidence/chat-context-second-approved-budget-ledger.json`, with a batch
identity/deadline sidecar. Evidence discloses scripted seeds separately from every
actual live turn and stores ordered requests, responses, usage and UTC timestamps.

Before live execution the offline rehearsal discovered and corrected test setup
problems: a general reply instruction needed a router response the old fake generator
did not implement; normal recipient clarification was required; Calendar needed the
actual granular read scopes persisted with a new JSON list; the fake transport had
to replace an existing transport argument; timestamp assertions needed Melbourne
conversion; and task-count checks needed a before/after baseline because previews
also create local tasks. These were harness/fixture corrections, not production
changes or real-model outcomes. The rehearsal blocks all AWS calls.
