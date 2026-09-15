# Bedrock workflow runtime and completion checklist

For the current coordinator, classifier boundary and planned multi-intent dependency
graph, see [master workflow](master-workflow.md).

This change completes the local backend invocation path for the three existing
single-operation generation workflows: summarise, reply and compose. It is **not
completion of the five-intent product or the complete B17 package**. The six Flows
from PR #13 are console prototypes with a different output contract; they cannot
be enabled through this runtime registry unchanged.

## Implemented lifecycle

```mermaid
sequenceDiagram
    participant UI as Frontend
    participant API as FastAPI
    participant DB as PostgreSQL
    participant W as Durable worker
    participant AWS as Bedrock Flow
    UI->>API: POST /assistant/requests
    API->>API: Authenticate, own context, bind draft envelope
    API->>DB: Save task, pinned registry and job atomically
    API-->>UI: 202 + task/event URLs
    W->>DB: Claim with expiring lease
    W->>W: Route whole supported operation
    W->>DB: Save route checkpoint
    W->>AWS: Verify published alias, version, role and exact graph
    W->>AWS: Invoke with masked backend prompt + bound excerpts
    AWS-->>W: Output followed by SUCCESS completion
    W->>AWS: Verify alias did not change during invocation
    W->>W: Validate artifact schema and source numbers
    W->>DB: Fence cancellation/lease, save artifact + provenance
    UI->>API: Read events/task/artifact
    API-->>UI: Source-linked summary or editable draft
    Note over UI,AWS: Draft review does not send mail; no external write tools exist here.
```

Facts and identity stay in the backend. The model sees numbered excerpts, not
provider IDs or the draft recipient envelope. The existing cloud masking policy
runs before invocation. Validation and evidence binding reuse the existing native
summary/draft functions, retaining partial-coverage notices and exact reply subjects.
Human edits/review use the same artifact APIs. No additional table or migration is needed.

This first runtime release supplies owner-bound context **before** the Flow rather
than using a Lambda read callback. Its exact graph is Input → Prompt → Output;
backend validation follows Output before publication. The manifest rejects arbitrary
Flow graphs, extra nodes, other models, test aliases and mutable draft versions.
This avoids exposing a read endpoint or credentials to the Flow. It does not implement
the callback/grant or compound-workflow portions of B17; those remain separate work.

## Registry and compatibility

`ASSISTANT_WORKFLOW_MANIFEST` is optional JSON in both API and worker environment.
Empty keeps the existing native acceptance behavior. A nonempty manifest has
`schema_version: "1.0"` and exactly three operation entries: `summarise_thread`,
`draft_reply`, `draft_new`. Each is either `{"implementation":"native"}` or a complete
published target validated by `app.workflows.registry.FlowEntry`.

Each accepted task stores its full registry, backend contract hash and native
routing release. Changing the active manifest affects **new tasks only**. Retries
of saved Flow tasks retain their original target even if the current registry is
rolled back to native. Keep old AWS versions, aliases and required permissions
until those tasks finish or are cancelled. Unsupported/changed backend releases
fail as `release_unavailable`; they are never silently interpreted as a new release.
Historical native summary/contextual tasks keep their existing implementation when
their original configuration is still available. Existing historical manifests
contain configuration hashes rather than recoverable provider settings: changing
the router/provider/model settings can make old queued releases unavailable.
Drain or cancel affected tasks before such a migration; this PR does not recover
provider settings from those hashes.

Flow targets pin region, account/Flow ARN, numbered version, alias ID, execution
role, Haiku profile, graph hash and input/output/event/time budgets. Alias/version
and graph are checked before every invocation; alias identity/routing/updatedAt
are checked again afterward. API response request IDs are ignored for comparison.
Alias mutation can race an invocation; restrict mutation to the deployment role,
never grant it to the worker, and do not treat the checks as an AWS transactional
version condition. The runtime publishes no result after detected movement.

The AWS SDK is imported lazily and invoked off the event loop, with no automatic
SDK retries. Event count, input/output byte sizes and total await time are bounded.
Cancelling an await sets a background stop flag; a synchronous SDK read can finish
later (bounded socket timeout), but it cannot dispatch another call or publish after
the worker loses its lease. Fenced artifact publication remains backend-owned.

## Failure semantics

| Condition | Saved outcome |
|---|---|
| Alias/version/role/graph changed | Non-retryable `workflow_release_changed` |
| Stream ends without SUCCESS | Retryable `incomplete_flow_output`; no artifact |
| Duplicate/wrong output node, unsupported content, extra terminal event | Non-retryable `invalid_flow_output` |
| Trace/unknown event returned | Non-retryable `invalid_flow_output`; trace content is not saved |
| Model output has invented source numbers/invalid artifact | Existing `invalid_summary_output` / `invalid_draft_output` |
| Rate limit or selected transient service failures | Bounded durable retry; no local SDK replay loop |
| Access denied/missing resource/invalid upstream request | Non-retryable `workflow_upstream_rejected` |
| Total deadline exceeded | Retryable `workflow_timeout`; late publication fenced |
| AWS multi-turn/input-required event | `workflow_input_required`; no output published |

Multi-turn events are unexpected in this restricted graph and currently fail with
an explicit code. They are **not** implemented as a resumable user question.
B08 must add durable clarification/continuation before enabling multi-turn graphs.
No provider body, prompt or trace is included in error messages/logs.

## Frontend contract

The existing request, task/event and artifact APIs are unchanged.
`GET /assistant/workflows` requires authentication and lists the three installed
operations and their configured `native`/`bedrock_flow` implementation. It does not
return AWS identifiers. `external_actions: false` and
`remote_resources_verified: false` distinguish configuration from live readiness.
This endpoint describes installed generation paths, not per-user Google grants;
the separate B01 capabilities endpoint is still being implemented on another branch.

`/readyz` adds `workflow_configuration`; malformed enabled configuration yields
503. This is local manifest validation, not proof that AWS model access or quotas
are healthy. Actual remote targets are checked at invocation. `/healthz` remains
independent of all dependencies.

## Prepare a runtime-compatible AWS release

1. Install this backend revision and its dependencies in a development environment.
   The following renderer makes no AWS calls. Use the actual Australian Haiku profile
   ARN from the prototype setup's local manifest; never commit that manifest.

   ```bash
   python -m app.workflows.release --profile-arn "$THREADLY_PROFILE_ARN" \
     --output runtime-flow-bundle
   ```

2. Inspect the generated `stack.json` and `flow.json`. The new stack contains three
   application-compatible Flows and its **own** execution role. It does not mutate
   the six prototypes, their role or the EC2 stack. Validate `stack.json` with cfn-lint.
   In the authorized AWS deployment environment, deploy that reviewed template with
   CloudFormation and `CAPABILITY_IAM`. Choose a new content/release-specific stack
   name; do not update an active application's graph in place.

3. For each new Flow, call PrepareFlow and wait for Prepared. Publish a numbered
   version using CreateFlowVersion and create a release-specific alias using
   CreateFlowAlias pointing to that version. Keep the exact graph/role/model and
   verify the resulting alias. Do not select `TSTALIASID`. This release uses the
   backend's native prompts, so the earlier prototype outputs cannot be substituted.

4. Create a private `targets.json` mapping the three operation names to full target
   objects. Each target contains `implementation: "bedrock_flow"`, `region`,
   `flow_arn`, `alias_id`, `version`, `model_profile_arn`, `execution_role_arn`, and
   `definition_hash` (the backend `digest` of `flow.json`). The published version
   must be the exact backend-rendered graph. Use renderer/schema tests as a synthetic
   example; replace all synthetic identities with actual outputs.

   ```bash
   python -m app.workflows.release --targets targets.json --output runtime-candidate
   ```

   The assembler rejects missing/unknown operations, malformed identifiers, DRAFT,
   wrong-region/profile/account, incorrect graph hashes and unsupported budgets.
   It emits `candidate-registry.json` and a narrowly scoped `worker-policy.json`.
   Neither is automatically attached or enabled. The worker policy allows only
   InvokeFlow on those aliases and GetFlowAlias/GetFlowVersion verification. Existing
   router Converse permissions remain separately necessary.

5. After authorization for billed synthetic tests, grant the test principal the
   scoped permissions and run the exact candidate:

   ```bash
   python -m app.workflows.evaluate \
     --manifest runtime-candidate/candidate-registry.json \
     --output runtime-smoke.json --invoke
   ```

   This performs three synthetic invocations, one per operation, and validates actual
   output against native artifact schemas. It uses no Gmail/Calendar API. A 3/3 result
   is a smoke test, **not** language-quality approval. Review factual accuracy,
   unsupported commitments, uncertainty, prompt injection and native/Flow parity
   with the broader replay suite before enabling an operation.

6. Attach the reviewed worker policy and install the approved registry JSON in the
   protected environment used by both API and worker. Configure an available Bedrock
   router model too. Deploy the tested backend commit through the existing release
   procedure. Check readiness and send synthetic requests through the authenticated
   API/worker path; merely running the direct evaluator does not test deployed jobs.

7. Roll back new admission by restoring the previous registry (or empty/native),
   retaining old AWS resources for queued jobs. Stop/cancel affected jobs explicitly
   if continued invocation is undesirable. Never remove the EC2 data volumes or
   recreate mailbox state as part of a Flow rollback.

## Everything still needed for the complete workflow

Completion is tracked by concrete gates, not a claimed percentage of total effort.

| Gate | Current evidence | Remaining work and owner |
|---|---|---|
| Six visual prototypes | User reports all six Prepared | AI can experiment; not production contracts |
| Durable summary/reply/compose Flow execution | Implemented and locally replayed | Backend/AI: AWS compatible release + live validation + deployment |
| Versioned generation registry | Implemented for three existing operations | Backend: extend only with implemented/evaluated new operations |
| Google capabilities/auth | Separate B01 work is unmerged | Backend: actual grants, refresh/revocation fences, consent verification |
| Selected screen context and follow-ups | Saved thread context only | B07/B08: ordered UI mapping and durable clarification/continuation |
| Bounded other assistance | Classified, execution unavailable | B09: owned lookup/transform/help handlers and source-linked answers |
| Compound plans | Unavailable | B10: step checkpoints, artifact streams and final-result identity |
| Action planning | Prototype only | B11: validated plan artifacts, dependencies and explicit assumptions |
| Calendar scheduling | Prototype wording only | B12/B13: Calendar preferences/freebusy, anchored time/DST, deterministic persisted slots |
| Approved sending | Draft editing/review only | B02–B06: durable actions, exact MIME preview/approval, separate dispatch and uncertain-write reconciliation |
| Approved booking | Unavailable | B14/B15: slot selection/rechecks, exact event approval and reconciliation |
| Production quality/release | Offline suite only for this change | B18–B20: live eval, failure/load recovery, IAM review, controlled deployment and frontend end-to-end tests |

The critical path for all five intents is B01 + B07/B08 → B09/B10 → B11/B12/B13,
with B02–B06 and B14/B15 completing external actions. The current invocation layer
can be reused throughout; no need to invent another router microservice or give
Bedrock authority to send/book. Optional proactive monitoring, voice, attachments,
style personalization and recurring-event mutation remain B21–B25 after the core.

## Concise summary policy update

New tasks use the versioned summary-quality wrapper described in
[summary-quality.md](summary-quality.md). The runtime graph contract stays unchanged:
it receives the backend's complete prompt. The separate one-Flow console experiment
has a fixed prompt and input adapter and cannot be used as a runtime registry entry.
Older jobs retain the old validator. Live semantic evaluation is still pending.
