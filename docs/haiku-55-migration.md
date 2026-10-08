# Haiku 5.5 compatibility and cutover

Status on 8 October 2026: compatibility prepared; production remains on Haiku 4.5.
Base/live backend verified as `8970bd4b7cb8ce504a672b6eb37d275ef8b14971`, schema
`h071026e9043`. The active-chat-context and Calendar date/title fixes are preserved.

## Actual call paths

| Path | Live transport | Migration |
| --- | --- | --- |
| Chat/tool decisions | `ConversationModel`, Converse with forced tool choice | Explicitly disable thinking, omit temperature, reject assistant prefill and unexpected signed reasoning |
| Summaries, drafts, routing and auxiliary generation | `ModelClient` → `BedrockProvider`, Converse | Same request policy; select text blocks, reject incomplete/refused/tool-only responses |
| Email badges | Numbered managed CHAT prompt + pinned classification Flow | New prompt/release fingerprint; explicit 5.5 model option; preserve old target |
| Direct classification baseline | `ClassificationProvider`, Converse | Same non-thinking policy and text selection |
| Historical workflow/prototype Flows | Not configured in production | Existing 4.5 assets remain pinned; no automatic rewrite |
| `backend/tools/*` CountTokens evaluations | Offline/manual harnesses only | Closed 4.5 ledgers unchanged and not reusable for 5.5 |

`haiku.request_options` contains only the model-specific request difference; it
does not select models, change endpoints or implement fallback. For 5.5 the output
allowance is rounded up by 30% (chat 2340; classification 1950; native generation
uses the caller's bounded limit × 1.3). This is headroom, not a quality or cost claim.
Existing text/input limits, tool validation, PII masking, action approvals and
Google execution remain unchanged. Thinking stays disabled to preserve existing
behavior; there is no new signed-reasoning history protocol.

The preflight accepts only full account AU 4.5/5.5 profile ARNs in the configured
Australian region. Published classification targets still require matching
account, prompt, graph, role, alias and version. Existing 4.5 requests/prompts
remain unchanged for explicit rollback.

Render a separate classification candidate without AWS calls:

```sh
PYTHONPATH=backend python infra/bedrock/classification_flows.py \
  --model anthropic.claude-haiku-5-5 --render-only --output /tmp/haiku55-candidate
```

Do not run provisioning until its IAM expansion is approved. The CLI defaults to
4.5, and does not activate the application. Keep the old prompt/Flow/alias and
role intact. New 5.5 prompt settings are hashed into a distinct candidate.

## Deployment gates and rollback

The account audit found zero applied 5.5 regional TPM, unavailable agreement,
and roles without the 5.5 AU profile/destination grants. No terms, quota, IAM,
logging or region settings were changed. These gates must be resolved before
cutover. The existing 4.5 quota support case must not be reused or changed.

Required permission review: API role `bedrock:InvokeModel` on the exact 5.5 AU
profile plus only Sydney/Melbourne 5.5 model ARNs, with the existing inference
profile condition. A separately pinned classification candidate needs its model
and managed-prompt grants plus the API's exact Flow/prompt read/invoke grants.
Use the rendered target/caller policy for review; no wildcard expansion, Mantle,
token-count action, new region or change to Google/OAuth permissions is needed.

Before paid inference, obtain a fresh budget for four synthetic calls: forced
tool decision, tool-result continuation, native draft and classification Flow.
Proposed total cap: US$0.10, no retries or real Gmail/Calendar traffic. Use fresh
call/cost reservations and record actual usage; old evaluation ledgers are closed.
Mock checks establish protocol compatibility, not live provider/model quality.

After access and smoke gates pass, recheck live/base drift and exact commit CI.
Acquire `/var/lock/threadly-deploy.lock`, preserve the protected environment and
rollback image, verify a database backup and candidate readiness, then switch
both `BEDROCK_MODEL_ID` and `BEDROCK_SMALL_MODEL_ID` to the same reviewed AU 5.5
profile and install the pinned classification candidate manifest. Preserve schema,
other settings and unrelated services. Verify all three backend services, health,
readiness and effective model/Flow identity. On failure restore the prior image,
both 4.5 model settings and old classification manifest together. No silent model
fallback is implemented.

## Verification and sources

`tests/test_haiku55.py` covers request shape, token headroom, text/reasoning handling,
forced tools and continuation, prefill refusal, incomplete/refusal responses,
classification schema/masking, 4.5 compatibility and AU/account pinning. Deployment
and provisioning tests additionally cover candidate separation and routing drift.
Full-suite and exact-head CI results belong in the PR's validation receipt.

- [Anthropic migration guide](https://platform.claude.com/docs/en/models/haiku-5-5/migration-guide)
- [AWS model card and AU destinations](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-5-5.html)
- [AWS pricing](https://aws.amazon.com/bedrock/pricing/): the account's Sydney offer
  rate card returned standard input/output rates of 0.11/0.55 and long-context
  rates of 0.55/2.75, with unit label `Units`. Confirm the offer's billing unit and
  applicable terms before spending; do not substitute public global/base pricing
  or promise a per-task savings ratio.
