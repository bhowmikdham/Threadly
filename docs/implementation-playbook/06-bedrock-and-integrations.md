# 06 · Bedrock configuration and Google integration runbook

## What we are configuring

The target is one application-level router and several bounded workflow assets.
Use Bedrock Runtime for the router and typed model calls; use Bedrock Flows for
visible per-intent generation pipelines. Native handlers serve cache and exact
database lookups. The versioned registry selects the implementation per operation.

Do not require AWS multi-turn preview features for durable approval. AWS documents
multi-turn Agent nodes and asynchronous flow executions as preview; asynchronous
executions also have finite duration. Threadly persists human waits and invokes
bounded stages as needed. [Multi-turn Flows](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-multi-turn-invocation.html),
[asynchronous executions](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-create-async.html).

## Configuration inventory

These are proposed settings, not environment variables already read by the app.
Implementation updates `config.py`, `.env.example`, deployment secrets and tests
together. Use deployment secret references, never real values in git.

| Setting/group | Owner | Meaning / validation |
|---|---|---|
| `AI_BACKEND=bedrock` | Backend | Explicit provider mode; old chain remains only as deliberately configured development mode |
| `AWS_REGION` | Infrastructure | Initial target Sydney `ap-southeast-2`; verify model and feature access |
| Router/generation model IDs | AI + infrastructure | Benchmarked supported models/inference profiles; do not guess names from another account |
| Workflow manifest path/version | Backend | Maps operation to handler or flow ID/release alias; fail startup/readiness for invalid enabled entries |
| Input/output token caps and model timeout | AI + backend | Per task kind; total run budget includes repairs/retries |
| Tool bridge endpoint/auth | Infrastructure + backend | Fixed authenticated endpoint; no model-selected host |
| Google client/redirect/secrets | Backend + infrastructure | Existing app OAuth configuration plus validated redirects/state |
| Calendar scopes/capabilities | Backend | Granted permissions gate enabled operations |
| Worker concurrency/leases/retry limits | Backend | Initial bounded concurrency, recoverable claims, alert on dead letters |
| Feature flags | Release owner | Per-intent availability, writes, proactive mode, RAG, voice |
| Logging/retention settings | Release owner | Redaction and bounded trace retention |

Flows supports Sydney, but supported models vary by node and region. Record the
actual model/profile selected in the release manifest and test with the deployment
role. Cross-region/global profiles require an explicit data-location decision.
[Supported regions/models](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-supported.html).

## IAM and service connectivity

Separate identities for: backend invocation, flow service execution, Lambda
adapters and deployment/promotion. Runtime roles invoke only configured resources;
deployment roles can publish versions and aliases. Keep Google tokens in the
backend token store; Lambdas receive only scoped run references and arguments.

Flow role permissions cover its selected model/prompt and named Lambda resources.
Lambda invoke policies restrict source account/resource as supported. Tool bridge
auth maps service identity plus run grant to allowed read capabilities. Instance
or workload roles supply AWS credentials; long-lived AWS keys never enter extension
configuration or prompt assets. Test denied permission paths as part of `T03/T07`.

## Flow builder recipe

AWS separates preparation/testing of a working draft from immutable published
versions and aliases used by applications. Export and review graph definitions
before promotion. [Flow lifecycle](https://docs.aws.amazon.com/bedrock/latest/userguide/flows.html).

1. Create a development flow with the approved service role and Object input.
2. Add the node chain from the table below. Name nodes after stable responsibilities.
3. Define typed input/output expressions, separating model content from backend
   context/run references. Prompt nodes must not receive tool credentials.
4. Add validation Lambda nodes around model outputs. Configure named read adapters
   only; proposal graphs have no Gmail send or Calendar write node.
5. Add conditions that route known statuses to explicit output nodes: ready,
   clarification, unavailable or error. Never let a failed tool response fall
   through as empty successful data.
6. Test with synthetic fixture inputs and tool outputs. Inspect node data sizes
   and field mappings, including every error branch.
7. Export definitions, pin prompt/Lambda/model versions, publish the flow version,
   and create a release-specific alias.
8. Run the same fixtures through the backend adapter before enabling the flow.

| Workflow asset | Suggested node chain | Terminal result |
|---|---|---|
| Summary | Input → context read → summary prompt → validate → Output | Summary with evidence/coverage |
| Action plan | Input → commitment read → planning prompt → validate DAG/dates → Output | Editable plan |
| Scheduling | Input → extraction prompt → validate → condition → Calendar read/slots → offer prompt → validate → Output | Clarification or options/draft |
| Reply | Input → context read → optional style read → draft prompt → validate → Output | Reply draft |
| Compose | Input → recipient/fact validation → draft prompt → validate → Output | New-message draft or clarification |
| Other synthesis | Input → bounded retrieval → answer prompt → evidence validation → Output | Sourced answer |

Prompt, Lambda and Condition nodes support this structure. An Inline Code node
has no internet access and is not the Calendar integration mechanism. Use Lambda
for calls into tested backend services. [Node capabilities](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-nodes.html).

### Data mappings and structured output

Examples: `request.instruction` and a redacted evidence list go to prompts;
`context_ref` travels directly to read adapters; validated `slots` go to a reply
prompt as stable IDs and rendered labels; output returns `artifact_kind`, content,
evidence and unresolved fields. Verify exact Flow expression syntax in the exported
definition with AWS validation; pseudocode in this playbook is not an AWS import.

Native structured outputs are model/API-specific. Bedrock documents support in
Converse/ConverseStream and a subset of JSON Schema; schema conformance does not
prove factual truth. Do not assume every Flow Prompt node exposes the same options.
Use backend validation/repair, or a Lambda invoking the typed model wrapper if that
capability is required and unavailable in the selected node configuration.
[Structured output support](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html).

Keep separate backend schemas and provider-compatible schemas where necessary.
Backend schemas can enforce ranges and lengths that a provider schema subset
does not support. Test the transformation and preserve the stronger backend check.

## Release manifest and reproducibility

The [manifest example](examples/release-manifest.example.json) binds code revision,
contracts, prompt bundle, model/profile IDs, flow versions/aliases, Lambda versions,
tool schema versions and evaluation report. Placeholders intentionally fail live
deployment preflight.

Use a new alias per release and a backend registry pointing at that alias. Keep
old aliases available for active tasks; do not repoint a supposedly pinned alias
mid-task. Roll back new task routing to the prior manifest while already running
tasks finish with their recorded release or are explicitly restarted. A draft
working alias is never the production registry entry.

Prompt source remains in git. The current `ml/` handoff rule requires AI asset PRs
to stay in `ml/`; coordinate linked backend/infra PRs for contract changes rather
than renaming assets independently. During migration agree how exported flow
source and release manifests are reviewed across `ml/` and `infra/`. Console
experiments must be exported before they count as team deliverables.

## Google OAuth and capabilities

Reuse the current encrypted token store, but save actual granted scopes and
differentiate revoked consent from transient provider/network failure. Incremental
consent links to the current Google subject; deny accidental cross-account linking.
Validate state and allowed redirect URIs. Refresh tokens must survive responses
that omit a new refresh token. Serialize concurrent refresh where needed.

| Capability | Proposed minimum access |
|---|---|
| Read email for context | Existing Gmail read access |
| Generate local Threadly drafts | No additional Google write scope |
| Send through Gmail | Existing granted `gmail.send` capability |
| Persist/manage Gmail-native drafts | Separate `gmail.compose` capability if introduced |
| Check owned-calendar availability | `calendar.freebusy` |
| Check authorized shared calendars | `calendar.events.freebusy` when needed |
| Select calendars from list | `calendar.calendarlist.readonly` |
| Create events on owned calendars | `calendar.events.owned` plus calendar ACL checks |

The chosen scopes must match actual operations. Broader shared-calendar writes,
event reading or mailbox modifications require their corresponding permission.
Recheck current Google verification requirements for the selected scopes before
public rollout. [Google scope catalogue](https://developers.google.com/identity/protocols/oauth2/scopes),
[Calendar scopes](https://developers.google.com/workspace/calendar/api/auth).

## Gmail ingestion and notifications

First repair sync ordering/cursor continuity, parsed address equality and reply
metadata. Scope all reads by user, deduplicate message IDs and isolate concurrent
account sync. Retain source version and received time; handle missing/malformed
Date headers. Respect existing body-cleaning policy while preserving enough
separately stored headers/evidence for accurate replies.

Initial trigger can remain on-demand sync plus a bounded scheduled worker. Later,
Gmail watches publish change hints to Cloud Pub/Sub; the backend consumes verified
notifications and obtains actual changes through history. Renew watches and
retain periodic reconciliation. Notifications do not contain the complete email.
[Gmail push notifications](https://developers.google.com/workspace/gmail/api/guides/push).

Expired Gmail history produces a resync path; watch notifications and backfills
must not generate repeated historical suggestions. Account disconnect cancels
queued dependent work and prevents further token refresh/use.

## Calendar read/write behavior

Start with live free/busy; no full event mirror is required. Cache only within a
short read stage and recheck live before consequential actions. Calendar watches
and event sync are later invalidation mechanisms, not replacements for rechecks.
If introduced, renew channels, process every incremental page and reset only the
affected calendar cache on 410. [Calendar synchronization](https://developers.google.com/workspace/calendar/api/guides/sync),
[Calendar notifications](https://developers.google.com/workspace/calendar/api/guides/push).

Event preview includes calendar, title, start/end/zone, attendees, location and
notification behavior. A Meet link is not assumed: generating conference data
is a separately tested feature and provider result. Invites notify attendees
according to the approved settings; event creation does not establish acceptance.
Retry/reconciliation follows the action lifecycle in chapter 05.

## Promotion checklist

Validate configuration and scopes → run fake-provider contract tests → test
development AWS resources → publish immutable release assets → run integration
and failure fixtures → verify operational dashboards and budget → enable read-only
pilot → enable writes for pilot after hard gates → expand users gradually.
Capture actual resource IDs in deployment secrets/config, not this plan or chat.
