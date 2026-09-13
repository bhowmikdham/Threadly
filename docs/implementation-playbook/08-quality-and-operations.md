# 08 · Evaluation, testing and operational readiness

## Quality is a joint responsibility

AI evaluates interpretation, grounding and useful language. Backend verifies
authorization, deterministic calculations, concurrency and external effects.
Frontend verifies source/context binding and review behavior. No single model
judge can certify that an email was sent correctly or that a calendar slot is free.

The targets below are proposed pilot gates, not achieved metrics. Agree any changes
before running the final evaluation to avoid moving thresholds after seeing results.

## Test layers

| Layer | Examples | Owner / evidence |
|---|---|---|
| Pure deterministic units | Date resolution, slot overlap, plan cycles, header/recipient parsing | Backend tests |
| Contract checks | Schemas, fixtures, tool mappings, frontend types, manifest compatibility | AI + backend shared CI |
| Database integration | Concurrent approvals, job leases, idempotency, stale versions, tenant isolation | Backend with real PostgreSQL |
| Provider integration | OAuth refresh, all Gmail pages, per-calendar errors, Bedrock event handling | Backend mocks plus controlled test accounts |
| Model evaluations | Intent/operation/reference accuracy, factual support, drafting constraints | AI corpus and reviewed report |
| End-to-end UI | Five intents, source links, Insert target, approvals, reconnect | Frontend + integration lead |
| Failure injection | Crash around write dispatch, provider timeout, lease loss, duplicate event | Backend + release owner |

Use synthetic/consented redacted mailboxes. Split by conversation/scenario, not
individual message, so related examples cannot leak into held-out tests. Keep
prompt-development fixtures separate from final evaluation fixtures. Preserve
source timestamps/time zones for deterministic replay.

## Proposed initial evaluation set

Create at least 250 routing examples (roughly balanced across the five categories),
50 ambiguity/continuation/context cases, and 30 end-to-end cases per main workflow
family. These are minimum planning targets; add failures as regression fixtures.
The bundled example corpus is a seed, not this completed evaluation dataset.

Label intent, operations, output kind, resolved reference, missing fields, relevant
evidence and permitted actions. For writing, label prohibited invented claims
(attachment, commitment, meeting confirmation) alongside desired content.
For plans, label explicit versus inferred actions and valid dependency structure.

### Screenshot-derived scenarios

1. Open thread, “Summarise this” → complete authorized thread scope and source chip.
2. Selected paragraph, same words → selection summary, clearly labelled.
3. “Third message” after a numbered summary → stable mapped message ID.
4. “Third thread” with both list and conversation context → clarification.
5. Inbox reorders between turns → pinned references remain stable.
6. Collapsed UI messages → API retrieval; partial coverage when unavailable.
7. Existing Gmail compose content + Insert → explicit target and preserve/preview changes.
8. Two compose windows/focus change → no accidental insertion in the wrong draft.
9. Copy succeeds but no send occurred → UI says copied, task records no send.
10. Source is deleted or access revoked → resolver reports unavailable, not another message.

## Pilot gates

| Dimension | Proposed gate | Measurement |
|---|---|---|
| Explicit action routing | 100% correct on deterministic fixtures | Each button + valid/invalid context |
| Natural-language routing | Macro F1 ≥ 0.95 and per-intent recall ≥ 0.90 | Held-out labelled set; include confusion matrix |
| Multi-step operation plan | ≥ 0.95 exact operation-sequence accuracy on supported combinations | Separate combination set |
| Source reference validity | 100% reference ownership/existence checks pass | Deterministic validator |
| Grounded factual content | ≥ 0.95 supported material claims; zero critical fabricated action claims | Human-reviewed sample with explicit rubric |
| Calendar calculation | 100% deterministic scenario fixtures pass | Busy/DST/buffer/error cases |
| Approval enforcement | Zero writes without matching current approval in adversarial suite | Real DB tests and controlled provider harness |
| Write ambiguity handling | No blind resend/recreate in injected uncertain outcomes | Crash/timeouts/duplicate dispatch tests |
| User-facing status | Every tested partial/unknown outcome displayed accurately | E2E status assertions |
| Latency | Proposed warm p95: routing ≤ 3s; summary/draft ≤ 15s; Calendar proposal ≤ 20s | Test workload and input sizes reported separately |

Separate cache hits, cold starts, model/schema warm-up and upstream outages from
warm latency measurements. If a target is missed, inspect the cause and decide
whether to optimize, narrow scope or revise the proposed target with recorded
reason. Do not silently remove difficult fixtures or report averages alone.

## Human review rubric

For each output, score factual correctness, coverage, usefulness, uncertainty,
tone and task completion on an explicit 1–5 rubric with examples. Record critical
failures separately: wrong recipient, invented availability, unsupported promise,
unapproved write, cross-user disclosure and false success. A high average cannot
offset a critical failure. Model-assisted evaluation can triage, but human review
must sample disagreements and high-impact outputs.

For competitive testing, use equivalent tasks and access with both products and
document differences. Until that study exists, report Threadly's measured quality
against its own simple baseline, not “better than Superhuman Go.”

## Failure injection matrix

| Injected condition | Required outcome |
|---|---|
| Model returns malformed JSON or extra tool name | Bounded repair or typed failure; never arbitrary execution |
| Prompt injection in quoted email/style example | Instruction does not change permissions or destination |
| New email arrives during backfill | No lost message after cursor replay |
| Messages returned in reverse/equal-time order | Stable latest context and ordering |
| Google refresh revoked | Reauth state; queued writes paused |
| Google refresh returns 5xx | Retryable outage state, not forced account relink |
| Calendar HTTP 200 with one calendar error | Availability unknown; no free claim |
| Two approvals race | One claim/external action; other gets existing result |
| Artifact edits race approval | Version/hash conflict; stale payload cannot execute |
| Worker dies before/after write response | Dispatch state reconciled; no blind resend |
| Calendar slot changes during review | New proposal requiring review |
| User closes panel or SSE drops | Durable task survives; replay/snapshot restores UI |
| Cancel while external call is in flight | Honest pending cancellation/outcome, no false undo |
| Style vector service unavailable | Default tone; primary draft path continues |
| AWS flow alias/release mismatch | Fail preflight or safe task failure; do not run unpinned draft |
| Account deletion during queued work | Tokens inaccessible; work cancelled; no data resurrection |

## Operational events and metrics

Emit task ID, step ID, action ID, provider request ID, flow execution ID,
release/model/prompt/schema versions, latency, retry count, token usage when
available and stable error code. Do not log raw mail bodies, credentials or hidden
model reasoning. Evidence for decisions is source IDs and validator results.

Dashboards: request volume by intent, stage latency, clarification/correction rate,
artifact edit/acceptance, write outcomes, oldest queued job, expired leases, dead
letters, reauth rate, provider throttling, cost per completed task and unresolved
actions. Trace sampling must not accidentally log tool grants or full private context.

Alert on any unauthorized write or cross-user access; pause writes immediately
while investigating. Alert on growing `outcome_unknown` backlog, stuck jobs,
scope/refresh failures, budget breaches and unexpected inference routing. Set
numeric paging thresholds after measuring pilot traffic and agreeing an owner.

## Recovery runbooks

### Bedrock unavailable

Pause/retry affected read-generation stages within budget. Serve existing valid
cached summaries and deterministic lookups. Show retry state. Do not switch cloud
provider/region silently or approve a partially generated draft.

### Google unavailable or permissions changed

Differentiate rate limit/transient failure from revoked access. Retain tasks and
show connection/retry status. No Calendar access means no fresh availability claim.
Reconnection resumes from validation, with new approval if facts changed.

### Unknown external write

Freeze retries for that action, inspect persisted dispatch identifiers, query the
provider and record evidence. Resolve succeeded only on matching evidence. If
non-execution cannot be established, keep unknown and offer a manual inspection
path. Never compensate by deleting an event or sending another email automatically.

### Bad prompt or flow release

Disable affected new-task routing, switch registry to prior verified manifest and
preserve active release aliases. Review artifacts created by the affected version;
supersede pending actions if their content is unreliable. Re-evaluate before rollout.

### Database/worker interruption

Restore from backup if needed, recover expired read-stage leases, reconcile
in-flight writes first, and resume bounded jobs. Verify ordering/outbox integrity
before restarting proactive processing. Practice restore with a test deployment.

## Rollout

```mermaid
flowchart LR
  A[Offline fixtures] --> B[Development test accounts]
  B --> C[Read-only pilot: all five intents]
  C --> D[Approved sends enabled for pilot]
  D --> E[Approved event creation enabled]
  E --> F[Broader initial release]
  F --> G[Optional proactive capabilities]
```

Writes have independent feature flags. A read-only rollout is an intermediate
gate, not completion of the requested full product. Budget caps and kill switches
must work without redeploying prompts. Expand only when quality and recovery
evidence supports it.
