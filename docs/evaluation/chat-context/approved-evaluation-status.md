# Approved evaluation: complete budget, incomplete behavioral coverage

The user approved the synthetic diagnostic at **2026-10-07 10:58:05 UTC**
(`Sentinel_fb4c03919edc8191859ae3334d1b081c`), including application prompts and
schemas sent to AWS. All **18 / 18 paid attempts** are accounted for in the persistent
ledger: **17 completed and one throttled**. **No attempts remain.** No counter was
reset across retries. This is a diagnostic result, not a release pass.

The evaluated prompt was `contextual-conversation-1.8.9+chat-context.2` at backend
checkpoint `4200a91820e8473f49e487caa4774bdaa402624d`; subsequent preflight/credential
harness changes did not change that prompt, its tools, the model or synthetic cases.
At completion of this first batch, `.3` had mechanical verification only. The
subsequent [second-batch results](second-stage-results.md) now record its live failures
and specific improvements. This first ledger and its original outcomes are unchanged.
No publication, merge, deployment, real Gmail/Calendar writes or permission changes
were performed. API/worker/frontend voice fixes remain preserved.

## CountTokens cause and recovery

The exact AWS error was `ValidationException`: "The provided model doesn't support
counting tokens." The inference-profile ARN failed at **11:11:30 UTC**, request
`4797f4ba-f76e-4b98-8442-d0105fdcd759`; the Sydney foundation-model ARN failed with
the same error, request `7a154e23-d850-4a55-b239-853029e15832`.
`GetInferenceProfile` confirmed that the profile uses Haiku 4.5 in Sydney/Melbourne.

The **bare foundation ID** `anthropic.claude-haiku-4-5-20251001-v1:0` succeeded on the
same Sydney runtime endpoint. The unchanged complete prompt/tool payload counted
**24,643 input tokens** at **11:14:36 UTC**, request
`1b627e8e-4dfe-4c34-8614-5edb9b30bdd8`. Thus the observed blocker was the accepted
identifier form, not excessive input or an unsupported underlying model. This does
not establish why AWS rejects its ARN forms internally. The free preflight uses that
bare ID; inference continues to use the original profile ARN and region below.
See [preflight evidence](count-token-resolution.json) and the historical
[initial failure](approved-preflight.json).

Inference model, unchanged:
`arn:aws:bedrock:ap-southeast-2:710507379899:inference-profile/au.anthropic.claude-haiku-4-5-20251001-v1:0`.
Endpoint region: `ap-southeast-2`. The AU profile can route within its configured
Australian regions; this diagnostic did not change that routing configuration.

The local harness initially also blocked the AWS `signin/CreateOAuth2Token`
credential refresh. It now resolves the existing session before installing its
Bedrock-only dispatch guard and holds frozen credentials only in memory. AWS then
reported that the refresh token had expired. The user renewed the existing login;
the same pending synthetic request resumed. Those setup failures made no additional
paid inference calls. No broader AWS operations were allowed through the inference
guard, no new grants were added, and credentials are absent from the evidence.

## Human review of actual model behavior

| Case | Observed success | Failure or unobserved behavior |
| --- | --- | --- |
| Long history and later correction, calls 1–6 | Recalled Project Lumen beyond the recent window; inspected the keyword-free turn-30 correction; answered violet/South, replacing North. | Follow-up draft call omitted generated text. Another recall consumed the sixth call; draft completion returned 503 at the diagnostic cap. |
| Two unfinished drafts, calls 7–12 | Asked only for Alex's missing message, retained separate Alex/Casey goals, then selected Alex's correct stable goal ID. | The new Casey call omitted `request_source`; generic repair wrongly steered it toward continuing Alex, which the backend rejected. A later repair created Casey correctly. Alex's completion omitted draft text; cap stopped further repair. Casey completion was not reached. |
| Source summary → reply → Calendar → return, calls 13–18 | Fresh-read the selected synthetic email; corrected missing citation after rejection and returned a grounded summary. On reply retry, respected the unread-source rejection and read the reference again. | One paid attempt was throttled. The final two calls prepared before reading, then read; no allowance remained to finish. User received the retained-request recovery message. No draft artifact, Calendar step or return-to-saved-summary step was reached. |

Overall: **0 / 3 complete end-to-end scenarios; useful partial successes, clear tool
contract inefficiencies, and insufficient coverage for release**. The six-attempt
per-case cap, including recall/repair calls, was too small for the attempted
multi-turn sequences; that does not prove they would pass with a larger cap.
The initial `pytest` harness reported one pass because it produced the report and
checked isolation; it did not grade model quality. The summary path used `respond`
rather than creating a durable summary task, so that run did not establish saved
artifact restoration. No generated draft's quality was evaluated.

Representative inference request IDs:

- Corrected recall answer (4): `1b85129d-95d4-4646-9c8c-c22d94b3c80e`.
- Casey omission (8): `dfb93c01-ad96-4210-ab11-21ab2e10cbfd`.
- Misguided continuation (9): `939e5863-b19a-4637-b503-0dc10f1439bc`.
- Alex selection (11): `f24add70-b8f1-4f8e-b6b5-2ee1d6cf2c53`.
- Grounded summary (15): `30c46303-941c-43d2-bd11-a6dbff133c20`.
- Retry prepare/read (17/18): `d45578c4-a3e8-4bd9-85f9-1131a4d28b71` /
  `be55d769-7d3d-4f3b-8c95-bc1898fd569e`.

The failed inference receipt records `ThrottlingException` but not its AWS request
ID or charge. Per-call wall-clock timestamps were not captured; receipt IDs and
ordered calls provide correlation without inventing times.

## Usage and cost

Completed usage: **460,564 input / 1,390 output tokens**, with zero cache tokens.
The [official AWS price feed](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/bedrockfoundationmodels/USD/current/bedrockfoundationmodels.json)
was checked at **11:00:10 UTC**: Sydney geo USD 1.10 / million input and USD 5.50 /
million output. Computed completed inference cost: **USD 0.51426540 before tax**,
**USD 0.56569194 allowing 10% GST**. Reserving the throttled attempt's full approved
32,000-input / 1,800-output maximum produces **USD 0.61530194 including that GST
allowance**, within USD 1. These are calculated estimates, not a reconciled invoice.
The failed request's actual billable usage was not returned.

Unused dollar allowance is **not** authorization for more calls: the 18-call cap
is exhausted. Account-specific tax/discounts were not inspected. CountTokens is
[free](https://docs.aws.amazon.com/bedrock/latest/userguide/count-tokens.html).
Each inference used successful counting, no SDK retry, the 32k/1.8k limits and the
persistent locked ledger. The 15-minute start deadline applies per invocation;
authentication recovery and the approved resume took place between invocations.

## Local repair and remaining gate

Commit `744982f` versions the prompt/tools as `.3`. It adds schema guidance to quote
the current user request, include generated draft text once recipient/purpose are
known, and preserve `continue_previous=false` when repairing a genuinely new goal.
The backend's `continuation_required` / `compose_required` feedback now explains
both new and retained cases. Ownership, source authority, field validation and
write approval checks are unchanged. A database-backed replay of the observed
Casey omission verifies both goals survive its repair; this does not demonstrate
that the real model will follow the revised guidance. The final full backend suite
at `744982f` passed **2,264 tests with no skips**; 62 focused tests also passed.
Both runs used isolated PostgreSQL and fake providers.

The next model gate identified after this first batch required new explicit approval.
That approval was subsequently granted for the separate second batch linked above.
The original recommendation was to evaluate `.3` on
held-out paraphrases and completed multi-turn cases, including draft quality,
source freshness, Calendar detours, saved-artifact return and injected/ambiguous
instructions. Size the budget for measured recall/repair overhead rather than
assuming six calls can complete each sequence. Do not rerun or reset this ledger.
Deployment remains separately gated by review, authorization and the coordinated
`h071026e9043` API/worker migration described in the design review.

[Machine-readable review](live-model-review.json) records usage, individual tools,
source hashes and the replay files. [Synthetic scenario outcomes](live-scenarios.json)
and [retry outcome](live-retry.json) retain actual responses. The compressed
[full synthetic request/response ledger](live-model-ledger.json.gz) contains only
application prompts, synthetic user/source data and AWS receipts, not credentials
or real mailbox history.
