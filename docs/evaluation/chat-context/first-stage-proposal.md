# First-stage model evaluation proposal — not executed

Status: approval proposal only. No CountTokens request or paid inference has run.
No publication or deployment is included. This replaces the earlier 48-call proposal.

## Exact model and destination

A read-only check at **2026-10-07 10:26:02 UTC** found both production API and
assistant-worker running backend `bc12ef106659f5ac8b5b79890e0887f1431e29ea` with:

- Provider: `bedrock`; region: **`ap-southeast-2` (Sydney)**.
- Main and small model: **`arn:aws:bedrock:ap-southeast-2:710507379899:inference-profile/au.anthropic.claude-haiku-4-5-20251001-v1:0`**.
- Production prompt: `contextual-conversation-1.8.8+calendar-intent.1`.
- Proposed local prompt: `contextual-conversation-1.8.9+chat-context.2`.

This uses the exact deployed model/profile, not an inferred model name or a model
switch. The AU geography profile can route within Australia; requests enter Sydney.
The local prototype is what changes. The model/prompt distinction is deliberate.
Read-only SSM receipt: `24203e5d-74d9-4f59-84c4-6b026ac2a1b9`, recorded in the task's
`evidence/chat-context-production-model-result.json`. No environment values changed.

## Three synthetic cases

The harness drives real conversation persistence, tool selection and generation
against isolated PostgreSQL and fake Google. It supplies no prescribed tool calls or
expected answer to the model. Each case gets **at most six inference calls**,
including auxiliary interpretation and worker generation. Total: **18 calls**.

| Case | User turns and question being tested |
| --- | --- |
| Long history and correction | Seed 45 exchanges, with a violet kit/North gate in turn 1 and keyword-free South correction in turn 30. Ask for those arrangements, then an email for Alex. Does retrieval find original details and apply the later correction? |
| Two unfinished drafts | Start separate Alex and Casey emails; return to Alex with the sapphire-crate purpose, then Casey with thanks for the map. Are goals independent, selected correctly, and missing details clarified without repeating known ones? |
| Source/workflow detour | Summarize a selected synthetic email, request a reply, start a Calendar event, then return to the saved summary. Are live source identity, pending work and saved artifacts distinct, with no duplicate summary or inherited write authority? |

A case reaching its six-call limit is **incomplete**, never a pass. The remaining
catalogue (interleaved Calendar ambiguity, unknown historical clock, quoted/negated
commands), broader paraphrases and repeated trials require a later proposal.
This small run can reject a design or reveal tool-selection problems; it cannot
establish release readiness or a statistical success rate.

## Hard limits and cost

- 18 paid attempts maximum, six per case, one at a time; failed/unknown attempts
  consume their full reserved allowance. A provider or token-preflight failure
  stops the run. SDK automatic retries are disabled.
- At most **32,000 counted input tokens** and **1,800 output tokens** per attempt.
  Aggregate maximum: **576,000 input / 32,400 output tokens**. Existing lower output
  requests remain lower. The conversation output ceiling matches production;
  worker generation can be truncated and that is reported, not silently retried.
- No new call starts after 15 minutes. A call already in flight has bounded SDK
  timeouts (5-second connection and 45-second read); this is not instant cancellation.
- CountTokens must succeed on the approved profile before each paid attempt. An
  unsupported profile, oversized input or access error stops without a paid fallback,
  model substitution, new permission grant or cap increase. The roughly 90,000-character
  prompt/tool package has not been token-counted; viability under 32,000 is unproven.
- A shared botocore dispatch guard covers coordinator, auxiliary/default clients and
  workers. Other AWS APIs, models/regions, streaming/InvokeModel/Flows, caching,
  guardrails, priority tier and extra request features are rejected. No separate
  managed evaluation job, model judge, Provisioned Throughput or cloud compute is created.

The current [AWS Bedrock price page](https://aws.amazon.com/bedrock/pricing/) loads
[this official USD price feed](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/bedrockfoundationmodels/USD/current/bedrockfoundationmodels.json).
The checked publication is **2026-09-30T00:19:12Z**. Its Sydney “Geo and In-region
Cross-region Inference” table lists Claude 4.5 Haiku at **USD 1.10 / million input**
and **USD 5.50 / million output** tokens. The source rate codes are preserved in
`evidence/chat-context-official-pricing.json`.

| Charge | Maximum USD |
| --- | ---: |
| Input: 576,000 × 1.10 / million | 0.63360 |
| Output: 32,400 × 5.50 / million | 0.17820 |
| Total metered inference | **0.81180** |
| If Australian 10% GST applies | 0.08118 |
| Total including that GST | **0.89298** |

[CountTokens is free](https://docs.aws.amazon.com/bedrock/latest/userguide/count-tokens.html)
and [cross-region inference has no extra routing charge](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html).
A read-only logging-configuration check returned no configured model invocation
logging destinations. The harness creates no logging/storage resources; local
fixtures and reports have no AWS compute/storage charge. Existing account-level
billing, support plans, optional audit sinks and bank currency conversion have not
been audited. No absolute all-in invoice claim is made for those unknowns.
[AWS documents 10% GST for Australian accounts](https://aws.amazon.com/tax-help/australia/);
the account's actual tax registration was not inspected.

**Proposed approval ceiling: USD 1.00 for this stage, including the shown 10% GST
allowance.** The metered inference bound remains USD 0.81180; the spare allowance
cannot buy additional calls. If account-specific incremental charges/taxes make
USD 1.00 unsuitable, resolve that before invoking rather than expanding approval.
Refresh public pricing immediately before an approved run; stop if it is higher.

## Review and evidence

For every turn, record model/prompt/tool hashes, request IDs, tool results, goal IDs,
owned citation references, returned artifacts, latency, actual usage, failures and
clarifications. Record no real user/provider records. The reviewer grades each
question as pass, fail or not observed, with a trace reference and explanation:

1. Did the model retrieve the relevant original user detail and later correction?
2. Did it keep independent goals and return to the requested one without inventing
   recipient/date/source facts or asking for already established details?
3. Did it preserve current intent, historical context and fresh provider facts as
   distinct inputs, with no quoted source instruction becoming authority?
4. Did saved-artifact return avoid regeneration, and did every draft/event remain
   unapproved with zero external action jobs?
5. Did any output truncation, unsupported API, exhausted cap or provider failure
   prevent a meaningful conclusion?

Any authority/ownership violation is a fail. Mechanical assertions and aggregate
counts do not replace semantic review. A clean diagnostic still requires held-out
paraphrases, failure/retry coverage and integration/release gates before deployment.

## Permission that is still missing

The eventual approval must expressly cover **sending the repository's internal
system prompt/tool schemas plus synthetic scenario/tool-result text to AWS Bedrock
CountTokens and Converse in Sydney using the AU profile above**, within this budget.
It covers no real Gmail/Calendar data, credentials/grants, production settings,
provider writes, publication or deployment.

Automatic approval review rejected the earlier **CountTokens preflight to AWS
Bedrock** before execution because explicit authorization to transmit the internal
prompt and tool schemas was not established. It has not been retried or routed
through another executor. The opt-in environment string is a guard, not permission.
