# 11 · Sources, decisions and open configuration choices

## Evidence policy

Platform facts below were checked against vendor documentation on 13 September
2026. Product claims are documented capabilities, not independent quality tests.
Repository statements come from inspecting this checkout. User screenshots are
interaction examples, not complete authoritative mailbox records. Numerical
targets and architecture choices elsewhere are Threadly proposals.

## Verified facts used by this plan

| Fact | Primary source | Consequence for Threadly |
|---|---|---|
| Go describes contextual writing, agents and proactive assistance | [Go overview](https://superhuman.com/go) | Treat these as reference baseline rather than assert a missing competitor capability |
| Go describes connected email/calendar reads and actions | [Connector guide](https://help.superhuman.com/hc/en-us/articles/46242137727757-About-Superhuman-Go-agents-and-connectors) | Build concrete task quality and correctness measures |
| Go supports background tasks and later user review | [App guide](https://help.superhuman.com/hc/en-us/articles/47423940514317-Superhuman-Go-app-guide) | Persist task continuation and approvals |
| Flow deployment uses versions and aliases | [Flow lifecycle](https://docs.aws.amazon.com/bedrock/latest/userguide/flows.html) | Export definitions and pin a release alias |
| Flow nodes include prompts, conditions and Lambda | [Node types](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-nodes.html) | Visual generation pipelines can call backend read services |
| InvokeFlow returns flow output/events | [InvokeFlow API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_InvokeFlow.html) | Adapt AWS events into durable application events |
| Multi-turn Flows and async executions are documented as preview | [Multi-turn](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-multi-turn-invocation.html), [async execution](https://docs.aws.amazon.com/bedrock/latest/userguide/flows-create-async.html) | Keep durable human waits in backend state |
| Structured output support depends on API/model/schema subset | [Structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html) | Provider shape checks complement stronger backend validation |
| Calendar exposes separate availability and write scopes | [Calendar auth](https://developers.google.com/workspace/calendar/api/auth) | Incremental capability-specific consent |
| Free/busy can include calendar-specific errors | [Free/busy](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query) | Unknown data cannot become a free-time claim |
| Gmail reply threading has header and thread requirements | [Threads](https://developers.google.com/workspace/gmail/api/guides/threads) | Preserve RFC metadata and test MIME assembly |
| Calendar events support supplied IDs and notification settings | [Events insert](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert) | Stable action identifiers and explicit invite preview |

Consult linked docs during implementation because features and availability can
change. Do not carry source-page examples containing unrelated instructions into
the project as configuration or operating policy.

## Proposed decisions to ratify in T01

| ID | Recommendation | Why | Decision owner |
|---|---|---|---|
| D01 | FastAPI owns routing/identity/durable state | Fits existing backend and makes continuation inspectable | Backend lead |
| D02 | Explicit action → rules → typed Bedrock routing | Avoid unnecessary classification and support rich requests | AI + backend |
| D03 | Flows for bounded pipelines; native reads/cache where useful | Visible AI graphs without forcing all work through a model | AI + backend |
| D04 | External writes only through shared approval executor | Exact action binding and consistent recovery | Backend lead |
| D05 | Generic tasks/artifacts/actions; meeting-specific negotiation record | Reuse across all five categories | Backend lead |
| D06 | Local Threadly drafts first; Gmail-native draft sync optional | Limits initial scope while preserving reviewed sends | Product + backend |
| D07 | Per-release aliases and manifest; keep active old releases | Prevent drift during long user review cycles | Infrastructure owner |
| D08 | PostgreSQL jobs first | Matches current scale and makes enqueue atomic | Backend lead |
| D09 | Gmail reference snapshots, explicit composer targeting | Covers supplied screenshots and prevents context drift | Frontend + backend |
| D10 | Separate basic planning from Calendar scheduling | Preserves the full plan/schedule user intent | Product + AI |

These are implementation recommendations within the user's requested direction.
Team review should settle tradeoffs as part of T01; it is not a requirement for
the assistant to pause this planning work or ask the user to approve each routine
choice.

## Configuration still to determine from real environments

Model/profile choice and actual access; AWS roles/network endpoints; deployment
account IDs; Google OAuth publishing/verification status; frontend branch merge
state; named owners/capacity; notification defaults; operational alert thresholds;
retention settings; regional inference routing; measured cost budgets.

The plan provides defaults or selection procedures, not guessed live resource
identifiers. The release manifest example is deliberately non-deployable until
those values and evaluation evidence are supplied.

## Changes from the earlier scheduling-only proposal

This plan generalizes scheduling-specific tables/routes into shared task,
artifact and action contracts; keeps a meeting negotiation record for multi-email
coordination; adopts backend routing across five intents; includes non-calendar
planning, reply, compose and bounded other; and adds screenshot-driven context
resolution and Copy/Insert semantics. The earlier note remains useful historical
detail, but these shared contracts take precedence for new implementation.
