# 10 · Diagram atlas and worked traces

Use this page to explain the system in a team walkthrough. Each link points to a
diagram in its implementation chapter; the accompanying text states what the
diagram is meant to teach. Mermaid source remains in git so a coding agent can
update diagrams alongside code/contracts. In a viewer without Mermaid support,
the text explanations and step tables still convey the flow.

## Diagram directory

| Diagram | Read it to understand |
|---|---|
| [System overview](README.md#the-architecture-in-one-minute) | User instruction → route → generation → proposal → approved execution |
| [Deployment/components](02-architecture.md#components-and-deployment) | What lives in the extension, FastAPI/worker, PostgreSQL, AWS and Google |
| [Request-to-action sequence](02-architecture.md#runtime-sequence) | Which component persists, invokes, waits and reports results |
| [Intent routing](03-routing.md#the-front-door) | Explicit actions, natural language, continuation and clarification |
| [Context binding](03-routing.md#context-binding-requirements-from-the-supplied-screenshots) | How “this,” “third message” and source chips retain exact references |
| [Summary](04-workflows.md#w1--summarise) | Evidence retrieval, cache, long context and grounded output |
| [Action planning](04-workflows.md#w2--planschedule-action-planning) | Requests become an editable dependency plan and confirmed commitments |
| [Scheduling](04-workflows.md#w3--planschedule-calendar-assistance-and-negotiation) | Resolve constraints, offer slots, wait for reply and separately approve booking |
| [Reply](04-workflows.md#w4--reply) | Recipient/context validation, shared facts, draft and approved send |
| [Compose](04-workflows.md#w5--compose) | New message purpose/recipient resolution and separate Copy/Insert/Send |
| [Other](04-workflows.md#w6--other-bounded-assistance) | Named read/transform/help handlers and unsupported requests |
| [Data relationships](05-contracts-and-state.md#core-data-model) | Task, artifact, evidence, action and meeting ownership |
| [Task/action state machines](05-contracts-and-state.md#state-machines) | Artifact completion versus action execution and uncertain outcomes |
| [Build dependencies](07-delivery.md#dependency-map) | Which work packages unlock the next integration milestone |
| [Rollout](08-quality-and-operations.md#rollout) | Evidence gates between fixtures, read pilot, writes and wider release |

## Trace 1 · The screenshot's summary and third-message follow-up

Synthetic names and IDs below illustrate behavior; they are not extracted mailbox
ground truth from the supplied screenshots.

| Step | Input/state | Owner behavior | Persisted evidence |
|---|---|---|---|
| 1 | Open conversation contains 7 message references | Extension captures active thread and ordered references | Context snapshot `C1` |
| 2 | “Summarise this” | Backend validates thread ownership and fetches messages | Snapshot version and actual coverage |
| 3 | Summary requested | Flow summarises retrieved messages; backend checks sources | Summary revision `S1`, numbered source map |
| 4 | User sees summary and source chip | UI can open the same authorized conversation | Stable source references, not generated URLs |
| 5 | “What's in the third thread?” | Resolver sees possible message/list ambiguity | Clarification `Q1` |
| 6 | “Third message in this conversation” | Resolve pinned third message ID; fetch source | Bound message reference |
| 7 | Answer gives the message's acknowledgement | Backend formats exact snippet with author/date | Answer with evidence; no arbitrary rereading of current screen |

If the UI context clearly establishes a thread list and the user asks for the
third thread, use that pinned list. Clarification is for real ambiguity, not every
ordinal reference. If the list has reordered, the source map does not silently change.

## Trace 2 · A full combined request

User: “Summarise this, list what I need to do, and draft a reply with three meeting
times next week.” This exceeds a naive single-category label, but fits four
allowed operations: summary → action plan → suggest slots → reply draft.

| Stage | Output | Constraint |
|---|---|---|
| Route | `plan_schedule`, four operations, draft output | If duration missing, ask before availability stage |
| Summarise | Cited decisions/open questions | Original messages remain available to later stages |
| Plan | Proposed work and explicit commitments | Inferred promises require user selection before inclusion |
| Availability | Verified candidate slot IDs | User's selected calendars only; exact zone/duration |
| Draft | Grounded reply referencing selected facts/slots | Backend renders exact dates; no invented booking |
| Review | Editable artifact and proposed send | Recipients/content/assumptions visible |
| Execute | Recheck + Gmail send | Requires current exact approval |

The task may pause between plan and draft for commitment selection. This is saved
progress, not a failed flow. Reuse valid completed read artifacts while checking
freshness before any consequential action.

## Trace 3 · New email plus insertion

User asks for a project introduction. The backend resolves the intended recipient
and generates a new-message artifact. User opens a Gmail composer with existing
text and clicks Insert. The extension shows the target and replacement/append
choice. If another composer takes focus, it retains the original target reference
or asks the user to select again. Successful insertion records an insertion result;
the email remains unsent. Backend-approved Send is a separate action. A manual
Gmail send can be observed later through sync, with provider evidence.

## Trace 4 · Failure after approval

User approves a scheduling offer. Backend verifies the payload and current
availability, records dispatch and sends to Gmail. The connection times out.
Action state becomes `outcome_unknown`. The UI says the system is checking whether
the message sent. A worker searches/reconciles using stored Message-ID and context.
On matching evidence it records success; without adequate evidence it remains
unresolved and offers manual inspection. It does not ask Bedrock whether the
message “probably sent,” and it does not blindly resend.

## Terminology

| Term | Meaning in this plan |
|---|---|
| Intent | User's category of work |
| Operation | A named capability within an allowed plan |
| Task | Durable user objective with state and linked results |
| Flow run | One bounded AWS execution; can be a step within a longer task |
| Context snapshot | Validated source references, ordering, scope and version |
| Artifact | Summary, draft, plan, options or answer that the user can inspect |
| Action | A concrete proposed external mutation, such as sending an email |
| Approval | Recorded authorization for the exact stored action version/payload |
| Evidence | Message/entity/check references supporting a claim or operation |
| Negotiation | Meeting coordination across offers and replies, beyond one flow run |
| Release manifest | Pinned code/prompt/model/flow/tool configuration for reproducibility |
| Other | Bounded supported helpers, with clarification/unsupported fallbacks |

## Teaching order for a team kickoff

Walk through the overview and runtime sequence, then Trace 1 to establish context
correctness. Explain the five workflows and shared draft/approval services. Use
Trace 4 to show why PostgreSQL and reconciliation remain necessary with a visual
flow builder. Finish with the dependency diagram and assign the first work packages.
