# .7 offline correction: review the intended owned goal

The `.6` Calendar confirmation failure is reproduced and repaired offline.
Release: `contextual-conversation-1.8.9+chat-context.7`, based on `473ebd31`.
Runtime/test commit: **`30612dbd8c890ae0fc56944312af6556eaba7152`**.
No AWS requests, provider writes, release publication or deployment were made.
Live conversational acceptance remains a gate; this is deterministic runtime evidence.

## Proven failure and retained positive evidence

The final `.6` provider request, `581cadf6-a28d-4efc-89e5-15c3a177867f`, ran at
2026-10-08 01:44:44.771–01:44:46.489 UTC. After Calendar creation, revision and a
closing acknowledgment, “Where do I confirm it?” produced
`review_email_draft({"presentation":"status"})`. The backend accepted the wrong
domain and returned Gmail guidance despite there being no email draft. Calendar
fields and its proposed action remained intact. This is a review-target validation
gap, not another retained-field loss. Always-present email controls may have biased
the model; that remains a hypothesis, not a proven cause.

The [.6 results](context6-resumption-results.md) remain unchanged: Alex and Casey
completed independently; Calendar preview and the 3 pm revision passed; the closing
guard produced the correct user-facing acknowledgment. The live Alex repair repeated
purpose, so omitted-purpose retention remains specifically an offline replay proof.
These successes do not erase the final confirmation failure.

## Implemented boundary and audit

- `review_conversation_goal` requires an owned chat goal ID and the complete current
  USER source. The stored goal kind determines Calendar, email, task or proposal
  review. The model must ask when the reference is ambiguous; an ID and exact source
  establish scope, not proof of semantic interpretation.
- The old email-only tool remains accepted for compatibility but is no longer
  advertised. It requires an applicable unique goal or explicit selection this turn.
  A Calendar-only target produces typed `review_goal_required` repair; mixed unbound
  requests produce a normal clarification. Durable focus alone cannot choose among
  independent requests. Workflow/task aliases for the same work are deduplicated.
- No actual email source means no Gmail controls. Reviewing an unfinished email
  asks only for its missing recipient/purpose. Current saved artifacts and their
  save receipts are refreshed through the existing ownership checks.
- Calendar status/prose guards and email save/promise shortcuts use the same target
  boundary. A current cleared candidate cannot resurrect an older action. Calendar
  action reads validate owner and originating chat, then use the existing fresh
  action response. They do not recreate, approve or dispatch anything.
- Closed Calendar goals with an existing action remain reviewable by explicit ID,
  discoverable with `list_conversation_goals(include_closed=true)`. Review leaves
  them closed and does not restore their pending slot. Closed email goals remain
  unavailable for selection/review.
- Explicit review cards suppress unrelated cards/Calendar choices that service
  decoration might otherwise attach from earlier selection in the same turn.
  Proposal review replay reads the saved state without restarting planning.
- Historical task resumption was inspected: it already binds a task to an owned
  exchange and current USER source, and refreshes its owned current artifact.
  No change to its historical-exchange source boundary was needed.

The selection/persistence field-retention rules from `.6`, Calendar payloads,
approval modes, voice repair and frontend are preserved. There is no migration.
Prompt and tool hashes are pinned in the new `.7` snapshot; previous snapshots
and paid traces are immutable.

## Verification

`test_goal_review.py` first failed all three acceptance checks on unchanged runtime
code: the bad tool was accepted, irrelevant email controls existed, and mixed-goal
review selected a draft. See [red-before output](context7-red-before.log).
An initial fixture-only run lacked runtime capabilities; that setup was corrected
before this retained red run.

The exact replay reads `.6` calls 7–10 from the committed ledger: first preview,
3 pm revision, closing, then the actual wrong-domain call. Only the subsequent
goal-bound repair is scripted and uses the fixture's owned goal ID. It returns
the existing proposed Calendar action; its payload hash, version, state and goal
fields are unchanged. It creates no approvals or jobs. This proves the backend
boundary, not that the live model will select the new tool unaided.

Thirty new regressions also cover mixed-goal ambiguity across four routes, explicit
email/Calendar selection, missing email purpose, all ten Calendar action statuses,
real cancellation followed by review, source/owner/chat violations, stale candidate
clears, current saved artifact/revision behavior, cross-goal card decoration, and
read-only proposal replay. Three older draft-restoration tests now supply explicit
goal binding while retaining their artifact, receipt and isolation assertions.
The first full run reported 2 failures / 2,361 passes: a former unbound Calendar
status test expected generic prose rather than the new required ambiguity question,
and a unit-created draft had no owned chat row. The Calendar assertion now requires
the exact clarification and absence of the older action. The email test now uses
a persisted owned chat and additionally proves zero tasks, approvals and jobs.
No runtime change was needed for those two fixture/contract updates. The first
full log is retained as `context7-full-before-fixture-updates.log`.

**Final full suite: 2,363 passed, no skips**, one existing Starlette deprecation
warning, 291.64 seconds. Ruff and documentation validation pass. Checks, exact code
identity and source hashes are recorded in [verification](context7-verification.json).
All DB checks use disposable `threadly_meeting_email_test`, never a diagnostic DB.
Models/Google are scripted or fake; the new fixtures explicitly reject AWS calls.
Frontend runtime is unchanged at `86af12f5e26aca9cb8a9a8c2ba76e1d2cfdd60bf`, so its
existing checks were not repeated. Existing voice files remain unchanged.

## Precise remaining two-call proposal — not authorized or started

Two attempts are sufficient for a narrow confirmation-routing check with at most
one repair. They cannot establish broad conversational quality or live coverage of
every mixed/closed-goal variation. No new budget is requested.

After explicit permission to reopen one 15-minute window, pin the tested `.7` head,
verify unchanged ledger prefixes and pricing/auth/full-input preflights, and create
a fresh isolated synthetic segment. Reconstruct the already observed Calendar
preview/revision/closing with recorded scripted responses. Submit only
**“Where do I confirm it?”**, allowing at most **two total model attempts** including
repair, auxiliary, throttled and failed calls. Stop immediately on a terminal
semantic regression; do not retry/reset after it. Stop on success without spending
a second attempt unnecessarily. No original paid calls are repeated or replaced.

Use the same Sydney AU Haiku profile, 32,000 input / 1,800 output token caps, at least
8 seconds between attempts, no SDK retries, synthetic Google adapters and zero
provider writes. Every request, observation, receipt, token usage and charge appends
to the existing third ledger; preserve all ten prior entries and both older ledgers.
Success requires the existing 15:00 Calendar card, correct pending/confirmation
guidance, unchanged event/approval state, and no email guidance or new action.

The third ledger remains **10/12 calls, USD 0.34752047 including GST**. Calendar has
used **USD 0.13795452** of its USD 0.30 cap. At the previously verified prices, each
maximum-size attempt reserves USD 0.04961 including GST. Two would put the total at
most **USD 0.44674047** and Calendar at **USD 0.23717452**, below the existing USD 0.60
total / USD 0.30 per-case limits. Pricing must be rechecked before any authorized
resumption. The semantic stop and expired evaluation window remain in force now.
