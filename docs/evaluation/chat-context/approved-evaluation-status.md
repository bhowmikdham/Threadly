# Approved evaluation: stopped at token preflight

The user approved the disclosed synthetic evaluation at **2026-10-07 10:58:05 UTC**
(message `Sentinel_fb4c03919edc8191859ae3334d1b081c`). Approval includes application
prompts/tool schemas sent to AWS for counting and inference, at most 18 paid calls
and USD 1 including applicable GST. It does not authorize publication, deployment,
real provider writes, changed model/region/transmission, larger caps or new grants.

**Paid calls used: 0 / 18. Inference cost: USD 0. No behavioral scenarios ran.**
The entire approved call allowance remains, but the required preflight must first
be resolved within the scope or amended with permission. There is no model-quality
result to grade as pass or fail.

## Actual attempts

1. The prior automatic-review denial concerned missing permission to transmit the
   internal prompt/tool schemas. After the explicit user approval, the retry passed
   automatic review. SDK client construction then stopped locally with
   `MissingDependencyException`, before an AWS CountTokens request. The installed
   botocore version `1.43.108` declares the `crt` extra as `awscrt==0.36.0`.
2. That exact native dependency was installed into the isolated local directory
   `/tmp/threadly-context-eval-crt`; existing credentials, grants and production
   settings were unchanged. The same approved preflight then sent one CountTokens
   request, with no SDK retry.
3. AWS returned **`ValidationException`** for CountTokens at
   **2026-10-07T11:03:05.833269Z** (client-side start timestamp), request ID
   **`edb45d29-9541-4c41-87ee-5deb5ef6e655`**. No input-token count was returned.
   The evaluation stopped before Converse/inference. No alternate executor,
   model, region, endpoint or uncounted fallback was used.

Target: `bedrock-runtime`, Sydney (`ap-southeast-2`), exact profile
`arn:aws:bedrock:ap-southeast-2:710507379899:inference-profile/au.anthropic.claude-haiku-4-5-20251001-v1:0`.
Payload: the pinned Threadly prompt/tool schemas and one synthetic preflight
sentence. The [receipt](approved-preflight.json) retains hashes, model identity,
error code and request ID. It does not retain the AWS explanatory error message,
so the precise service-side validation reason is not proven by this receipt.

## What the documentation supports

The [CountTokens API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_CountTokens.html)
describes its `modelId` as a foundation-model ID/ARN; our approved inference target
is an inference-profile ARN. The [token-counting guide](https://docs.aws.amazon.com/bedrock/latest/userguide/count-tokens.html)
also warns that some CRIS-only Claude models cannot use runtime CountTokens and
require the different Mantle counting endpoint. These make target/API compatibility
a plausible explanation, not a proven interpretation of this particular error.

The [Haiku 4.5 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html)
requires an inference profile for runtime inference. It lists Mantle access in
Melbourne but not Sydney, while its general runtime feature table still marks
counting supported. Do not silently infer that a bare ID or different endpoint
will work, or switch regions based on those tables. Resolving that ambiguity is
the next diagnostic step; any change to the approved target/transmission requires
permission before another request. No access-policy change is proposed.

## Budget status

The official public AWS price feed was refreshed at **2026-10-07T11:00:10Z**;
Sydney geo prices remain USD 1.10 / million input and USD 5.50 / million output.
The unchanged maximum is USD 0.81180 before tax, USD 0.89298 if 10% GST applies.
[CountTokens itself is free](https://docs.aws.amazon.com/bedrock/latest/userguide/count-tokens.html).
No paid attempt was started, so there is no inference usage to subtract. An amended
preflight must still establish the 32,000-input-token cap before paid calls; spare
budget is not permission to bypass that requirement.
