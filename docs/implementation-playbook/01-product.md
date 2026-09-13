# 01 · Product behavior and target capabilities

## A shared definition of the product

Threadly helps a user understand email, decide what needs doing, prepare grounded
communications and complete explicitly approved actions. Its primary surface is
the Gmail side panel. Text, buttons and later voice are different input methods
for the same backend task system.

The unit of value is a completed user objective: a useful summary, a correct
reply draft, an actionable plan, a composed email, a sourced answer or an approved
send/booking whose outcome is known. Counting agent calls or AWS nodes is not a
product success metric.

## Reference baseline and what “better” must mean

Superhuman Go documents contextual email drafting, proactive suggestions,
custom agents and connectors. Its help material also describes Gmail/Calendar
search and actions. Those capabilities are reference expectations, not claimed
gaps in its product. [Go overview](https://superhuman.com/go),
[agents and connectors](https://help.superhuman.com/hc/en-us/articles/46242137727757-About-Superhuman-Go-agents-and-connectors).

Go also documents background tasks and returning later to approve or provide more
information. Threadly must make those behaviors reliable in its own implementation.
[Go app guide](https://help.superhuman.com/hc/en-us/articles/47423940514317-Superhuman-Go-app-guide).

| Target advantage to investigate | What we will build | How to establish whether it helps |
|---|---|---|
| Verifiable answers | Evidence attached to important summary/answer claims | Blind factuality review and source-opening success rate |
| Explicit uncertainty | Missing duration/zone/recipient surfaced before action | Ambiguity fixtures and user correction rate |
| Cross-message continuity | Preserve offered options, decisions and commitments | Delayed/reordered reply and pronoun-reference tests |
| Reliable completion | Persisted approvals, partial outcomes and reconciliation | Failure injection and task-completion observation |
| Useful planning | Dependencies, owners, dates and unresolved questions | User-rated actionability against a basic summary baseline |
| User control without repetition | Editable previews; approval only when action is concrete | Time and clicks to complete a task; needless clarification count |
| Predictable operation | Task budgets, graceful degradation and visible capability limits | Cost/latency distributions and outage drills |

Do not claim competitive superiority without a matched, consented task benchmark.
Use the same mailbox/calendar fixtures, instructions and success rubric; record
product version/date and disclose features unavailable in either test environment.
If competitor access is unavailable, compare against our own baseline and mark
competitive outcomes unmeasured.

## The five categories

| Category | User outcome | Initial release | Later extension |
|---|---|---|---|
| Summarise | Understand a thread, its decisions and open actions | Thread summary with message evidence and freshness | Cross-thread briefing and changes-since-last-read |
| Plan/schedule | Decide the work and find meeting times | Editable action plan; check time; propose slots; approved event creation | Recurring events, resources, rescheduling, cancellation |
| Reply | Respond within an existing conversation | Grounded editable draft, tone/rewrite, approved send | Approved follow-up sequences and attachment-aware replies |
| Compose | Start a new communication | Recipient disambiguation, subject/body, missing-fact handling, approved send | Templates, attachments and additional sources |
| Other | Get a supported answer or transformation | Email/entity/commitment lookup, help, selected-text rewrite/translation | More connectors and explicitly designed tools |

“Other” has named supported operations. Unknown requests receive a capability
explanation or clarification; they do not enter an unrestricted tool loop.
“Plan” includes non-calendar action planning, not only scheduling.

## User journeys

### Journey A · Understand and act

Open a long thread → choose Summarise → see decisions, owners, deadlines and
citations → ask “turn the open points into a plan” → edit plan items → ask for a
reply committing only to selected actions → review recipients/text → send.
No deadline inferred from vague wording becomes a confirmed commitment silently.

### Journey B · Coordinate a meeting

Email requests three times → user asks for help → duration/zone confirmed or
resolved from saved preferences → Calendar-backed options → editable offer →
approve send → later “the second one works” → recheck that offered option →
event preview → approve create/invite → show event outcome and attendee status.

### Journey C · Compose from scratch

User asks for an introduction email → choose recipient among ambiguous contacts
or enter an address → provide missing project facts → draft with source/context
labels → edit tone → approve exact content/recipients → send → expose Sent link.
No previous thread ID is attached to a new message.

### Journey D · Recover after interruption

Close the panel during work → backend continues → reopen task list → retrieve
current state → see ready result, requested clarification or action status.
If Google timed out after accepting a send, show “Checking whether it sent” until
reconciliation establishes an outcome. Do not ask the model to guess success.

## Interaction and permission model

| Situation | UI behavior | Backend requirement |
|---|---|---|
| Direct button action | Start selected capability | Validate task context; skip unnecessary intent inference |
| Natural-language command | Show a short task description and progress | Resolve intent; preserve user's words |
| Missing essential information | Ask one focused question with useful options | Save question and expected task version |
| Read-only result | Show answer, source links, coverage and freshness | Persist validated artifact |
| Draft/plan produced | Show editable artifact and possible next actions | Mark artifact objective complete; do not send automatically |
| User requests sending/booking | Present exact action preview | Require authenticated action approval |
| Proactive suggestion | Quiet card; explain why it appeared; dismiss/snooze | Opt-in, dedupe and suppression policy |
| Connection lost | Reconnect state with recoverable task | Retain safe state; execute no unavailable capability |

Clarification supplies missing facts. Approval authorizes a concrete action.
A user can clarify “30 minutes” without authorizing an email. A draft-generation
task can be complete while its send action has never been approved.

## Required UI components and fields

- **Task composer:** explicit action buttons, text input, active thread context,
  selected task continuation; voice later maps transcription to the same contract.
- **Task list:** status, last meaningful update, action required, reopen/cancel;
  multiple concurrent tasks never share one implicit “yes” target.
- **Evidence view:** claim → source message and relevant excerpt; show date and
  whether the source is superseded by a newer correction.
- **Draft editor:** To/Cc/Bcc, subject, body, tone; thread/reply target; revision;
  warnings only for actual unresolved facts or unsupported attachments.
- **Plan editor:** task, owner, due date, dependency and source/assumption status;
  internal plan edits do not imply sending promises to others.
- **Schedule card:** exact local date/time/zone/duration, calendars checked,
  assumptions, last checked time, up to three options and refresh.
- **Action review:** exact recipients/content/event/notifications, changed-fields
  summary since prior revision, approval button labelled with the action.
- **Outcome panel:** succeeded, failed, partial or unknown per action; provider
  resource links where available and the next safe recovery step.

## Initial boundaries

One connected Google account per user; selected owned/shared calendars according
to granted permissions. A sender's free time cannot be known from the user's
calendar alone. Gmail attachments can be acknowledged as unprocessed in the first
release; do not imply full-thread understanding when essential attachment content
is unavailable. No browser-wide screen access or autonomous external browsing is
required for the initial Gmail product.

Sending, inviting, deleting or changing external records is never a side effect
of read-only assistance. Mailbox triage writes, attachment processing and other
providers need separate scopes, contracts and release gates.

## Demonstration checklist for a complete initial release

Run all five categories from both explicit actions and natural language. Show a
combined summary→reply request, a non-calendar action plan, ambiguous recipient
clarification, missing Calendar permission, three-slot negotiation, cancellation
before execution, expired approval, panel reconnect and uncertain-send recovery.
Record results using the evaluation rubric, not a hand-picked happy-path video.
