# Classification backend rollout

Scope: deploy the Haiku classification workflow service for the frontend team to
call. Frontend request scheduling, badge rendering and browser release delivery
belong to that team. No extension or website implementation is part of this rollout.

## Release and configuration

Merge into `release/backend` and deploy an immutable commit whose complete source
tree passed backend CI. If using a pre-merge CI result, verify exact Git tree
identity with the merged commit; otherwise wait for the merged commit's CI.
Record the tested and deployed SHAs and the release-branch CI result.
Backend CI includes classification fixtures, Flow pinning,
provisioning and the rendered Haiku role template. Follow the existing
[EC2 deployment runbook](../../infra/deploy/ec2/APP-DEPLOYMENT.md), preserving the
host's current deployment mode. No classification migration is required.

For the current public host, Caddy mounts the website from a separate release.
Use a backend-only rollout: build/preflight the new API image, drain existing
backend services, take the protected database snapshot, check migrations, then
recreate only `api`, `assistant-worker` and `action-worker` with `--no-deps` using
the existing public HTTPS/launch Compose overlays. Verify the Caddy container and
its website/download mounts remain unchanged. Running the full public-launch
helper would also replace website/proxy assets and is outside this service's scope.
The [initial rollout record](ROLLOUT-2026-10-07.md) includes the actual outcome.

The existing private release receipt contains `haiku.target.json` and
`haiku.caller-policy.json`. Attach the latter as the separate named inline policy
`ThreadlyClassificationHaikuFlow` on the existing application instance role; do
not replace its other permissions or the Haiku Flow execution role. This policy
attachment is managed separately from the host's original CloudFormation stack.
It grants only the selected Flow alias invocation and pinned Flow/prompt reads.
Save the previous policy/configuration and deployed commit before rollout.

In the protected server environment file, set:

```text
CLASSIFICATION_ENABLED=false
CLASSIFICATION_TRANSPORT=bedrock_flow
CLASSIFICATION_FLOW_MANIFEST=<single-line JSON from haiku.target.json>
CLASSIFICATION_MAX_CONCURRENCY=2
CLASSIFICATION_REQUESTS_PER_MINUTE=8
CLASSIFICATION_VALID_SECONDS=3600
GMAIL_SOURCE_MODE=on_demand
BEDROCK_REGION=ap-southeast-2
BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED=true
```

The acknowledgement follows the existing mail-processing review; it is not a
technical check. The model is pinned in the Flow manifest. Do not put real mail,
OAuth tokens, AWS credentials or private deployment receipts into Git. The local
Converse comparison setting is not used by deployed classification.

## Activation and evidence

1. Deploy with classification disabled; verify authenticated calls return
   `classification_disabled`, unauthenticated calls return 401, and existing
   readiness and workers remain healthy.
2. From the application container, verify the Flow target using the instance role
   and replay the saved synthetic evaluation through that same published Flow.
3. Enable the protected flag and recreate the API. Deployment preflight rejects
   missing acknowledgement, invalid targets and non-Flow transports. Readiness
   checks the enabled classification configuration; it does not invoke a model.
4. Use the user-designated account through normal authentication for a bounded
   live Gmail test. Record only sanitized status/latency/contract evidence, not
   message subjects, bodies, account tokens or private thread IDs.
5. Verify unknown sources and revoked/expired sessions fail without badges.
   Exercise concurrent requests, check bounded rejection and inspect operational
   logs. Save a release receipt and give the frontend team the API handoff.

The current EC2 API is one process. Classification admission begins before Gmail
reads and is limited to two active requests; the SDK worker separately retains
its slot through cancellation. The rolling budget defaults to eight inference
attempts/minute, including retries, against the observed applied quota of ten.
This leaves limited headroom for other model consumers. Keep concurrency at two
until AWS confirms a higher applied request quota. It does not govern
other assistant model calls. There is no distributed limiter, deduplication or
cache; reassess the limit before increasing API workers/replicas. The frontend
must avoid repeated rendering-triggered requests and respect the actual variable
429/Retry-After. See [integration changes](FRONTEND-RELIABILITY-HANDOFF.md).
After a quota increase, choose a classification request budget below the shared
quota, then test concurrency four with synthetic and authorized live traffic.
Do not increase workers or concurrency to work around a requests/minute quota.

Operational log entries contain `classification_request`, outcome, sanitized error
code and duration in milliseconds. Provider failure entries include allowlisted
operation/code enums and retry counts; Gmail reads include allowlisted reasons.
They omit labels, raw provider messages and email content. Review
these alongside Bedrock invocation/throttling/token metrics in the source region;
the initial synthetic score is not a production accuracy or cost estimate.
Broader human-labelled evaluation remains ongoing product quality work.

## Rollback

Set `CLASSIFICATION_ENABLED=false` in the protected environment and recreate the
API using the recorded release's existing Compose configuration. Confirm the
endpoint returns `classification_disabled` and ordinary readiness still passes.
Keep the Flow and evaluation receipt for diagnosis. No label/database rollback
or destructive migration is needed. A code rollback must preserve compatibility
with the actual deployed schema and use the existing EC2 recovery procedure.

## Frontend handoff

Use [the endpoint contract](README.md), [TypeScript types](types.ts),
[response schema](response.schema.json) and [synthetic fixtures](frontend-fixtures.json).
The public route is `POST https://api.threadly.au/threads/{gmail_thread_id}/classification`
with the existing Threadly access JWT and `{"time_zone":"Australia/Melbourne"}`.
The client supplies an ID and timezone only; backend-owned Gmail retrieval and
Haiku classification produce reply, priority, category and action labels.
