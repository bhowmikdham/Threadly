# Whole backend progress — 16 September 2026

Audit baseline: PR #30 merged at `8cfd020de17b1b5c33d978db8e5e19cda4899279`
on `codex/assistant-intent-routing`. The current PR adds B10c reviewed compound-command planning.
This report separates implemented code, accepted package scope, and deployed/live
behavior. Frontend integration remains deferred at the user's request.

## Overall progress bar

Initial-release task board, **21 packages (B00–B20)**:

```text
[████▒▒▒▒▒▒▒▒░░░░░░░░░]
 █ Verified package: 4     ▒ Partial / in review: 8     ░ Planned: 9
```

Source: `docs/backend-execution/tasks.json`, checkpoints and runtime source audit.
Four packages (19% of the package count) are formally verified; twelve (57%) have
implementation evidence. **Neither figure is a percentage of engineering effort or
production readiness.** Packages vary greatly in size. In particular B06 has merged
recovery code but stays open for the live email gate; B10 has working templates but
not a full-command planner. B00 still needs its shared-contract settlement recorded.
B21–B25 are five additional planned capability extensions, outside that initial-release
bar. PR counts, prepared AWS Flows and passing tests do not close these packages.

## What works in code now

| Area | Implemented behavior | Evidence / remaining verification |
|---|---|---|
| Hosting and persistence | EC2 bootstrap/deploy tooling, PostgreSQL migrations, API, durable worker, disabled action-worker service | Host deployment was demonstrated; latest merged code is not confirmed deployed |
| Identity and Google capabilities | OAuth state/PKCE, actual granted scope storage, owner-bound token refresh/reconnect | B01 tests/checkpoint; controlled Google login and revocation smoke remains |
| Task lifecycle | Idempotent acceptance, owned captures, durable jobs, leases, retries, cancellation and replayable events | Real PostgreSQL lifecycle/concurrency tests |
| Summaries | Concise structured summaries, explicit evidence validation, versioned grounding policy | Offline fixtures and prior user console examples; current runtime holdout still needed |
| Reply / compose | Bound recipients/reply target, editable revisioned drafts, exact review hashes | Draft and revision suites; text generation is not mail sending |
| Context and follow-up | Saved UI ordering/reference mapping, durable typed answers, contextual time clarification | B07/B08 local cases; actual frontend/live interaction remains |
| Bounded Other / retrieval | Captured-text lookup, selected rewrite, explicit local-mailbox search with scope/cursors | B09 tests; general semantic lookup/extraction not complete |
| Compound tasks | Summary → reply/compose with separate streams, reusable checkpoint and final pointer | B10a merged/deployed in PR #23; live provider setup not verified |
| Lookup → draft | New explicit captured-text lookup → reply/compose; native checkpoint, scoped generation, no/too-many-match stop | B10b merged PR #30; captured-text-only scope |
| Reviewed command planning | Persisted all-word clause interpretation, prohibitions, dependency compilation and exact review → existing compound task | B10c current PR; four pairs only, live semantic gate and automatic routing pending |
| Bedrock execution | Native/Flow registry, pinned manifests, bounded InvokeFlow adapter, versioned prompts | B16/B17 code and synthetic SDK replay; cloud end-to-end gate still open |
| Email actions | Immutable action/approval records, exact MIME preview, explicit decisions, dedicated sender and read-only uncertain-outcome reconciliation | B02–B05 verified local scope, B06 merged recovery; public approval/sending remains gated |

## Complete package ledger

| Package | Recorded status | Remaining to close the package |
|---|---|---|
| B00 shared contracts | Planned | Record current baseline and agreed cross-team contracts; do not treat old baseline prose as current |
| B01 auth/capabilities | In review | Controlled OAuth/scope/revocation and callback integration evidence |
| B02 action storage | Verified | Retain live gate at the email-chain level |
| B03 MIME / preview | Verified | Retain live gate at the email-chain level |
| B04 approval decisions | Verified | Public enablement intentionally waits for email gate |
| B05 Gmail executor | Verified, disabled | Controlled end-to-end send evidence before enabling |
| B06 Gmail reconciliation | In review, merged | Live sent-message lookup/MIME semantics and full email gate |
| B07 UI references | In review | Actual client capture/ordering integration acceptance |
| B08 clarification | In review | Staging/client follow-up paths; broader plan clarification not installed |
| B09 bounded Other | In progress | Broader retrieval/extraction and natural-language query coordination |
| B10 multi-step workflows | In progress | Live planner semantic validation, automatic routing, broader lookup coordination, in-place clarification, Calendar combinations once available |
| B11 non-calendar planning | Planned | Evidence-backed plan revisions, owners/deadlines and explicit commitment selection |
| B12 Calendar reads | Planned | Actual Calendar grants, preferences, owned calendars, freebusy and freshness |
| B13 time/slots | Planned | Deterministic time resolution, timezone/DST handling, working hours, buffers, conflicts and bounded slot sets |
| B14 meeting negotiation | Planned | Slot offers, selection/expiry, negotiation state and refreshed availability |
| B15 Calendar writes | Planned | Exact event approval, execution, idempotency and uncertain-outcome reconciliation |
| B16 operation registry | In progress | Extend only when handlers exist; complete target operation/release coverage |
| B17 Flow read bridge | In progress | Current generation Flow adapter exists; authorized bounded read callbacks/full target integration remain |
| B18 all-intent quality gate | Planned | Shared all-intent model/API/client scenario matrix, adversarial and recovery evidence |
| B19 operations | Planned | Remaining retention, reliability/operational controls and runbooks backed by tests |
| B20 pilot | Planned | Controlled pilot, recorded defects/acceptance and original package closure |

Later capabilities: **B21** proactive suggestions/triggers; **B22** consented writing
style; **B23** voice; **B24** attachments; **B25** Calendar changes/recurrence/resources.
These are not implied by the current email generation/sending infrastructure.

The separate BERT handoff also needs review/integration: verify labels, domain,
activation and thresholds; confirm whether it classifies email content or user
commands. Model files in Git LFS are not proof a runtime adapter is installed.
Multi-label scores may advise routing; they cannot choose tools, source IDs,
recipients, Flow ARNs, execution order or permissions.

## What to implement next

1. Evaluate the reviewed B10c command planner against a live command-domain holdout.
   Extend typed corrected-plan continuation and broader retrieval/graphs; preserve
   every requested output/negation before enabling automatic routing. The 14-case
   synthetic corpus is a starting contract, not live quality acceptance.
2. Build B11 non-calendar plans with explicit commitment acceptance. Build B12/B13
   Calendar read/slot foundations against the established auth contracts; do not
   generate available times from model text.
3. Add B14/B15 meeting negotiation and approved event creation/recovery. Only then
   enable summary + slots + reply as a supported complete graph.
4. Complete operation/Flow coverage, model quality, staging failure/recovery tests,
   retention/operations and the pilot gates. Frontend integration rejoins when ready.

Live OAuth, Bedrock and Gmail verification can proceed alongside implementation
without waiting for every Calendar feature. Backend API-level tests can start now;
the complete five-intent release cannot pass acceptance while scheduling handlers
remain absent and broader planner/quality gates are incomplete.

```mermaid
flowchart LR
    A[Current: durable summaries, drafts, reads and email recovery code] --> B[Merged: lookup plus draft]
    B --> P[Current PR: reviewed compound-command planner]
    P --> C[Live planner quality, broader graphs and typed plan clarification]
    C --> D[Non-calendar plans]
    A --> E[Calendar grants, preferences and freebusy]
    E --> F[Deterministic slots]
    C --> G[Summary plus slots plus reply]
    F --> G
    F --> H[Negotiation and approved booking/recovery]
    A --> I[Live OAuth, Bedrock and Gmail gates]
    D --> J[All-intent quality, operations and pilot]
    G --> J
    H --> J
    I --> J
```

## Deployment and testing truth

Last confirmed host log in this task: PR #23 commit
`954b4926d5e0c4928ebecde06f2f67e918d5be06`, migration `c8291e4a6f03`, healthy API
and dependencies, but model/OAuth configuration pending in that log. Later merges
are not evidence of an EC2 update. Current merged migration head is `a0426e9bc731`; this PR adds `b10c026e9a31`.
The current PR does not deploy or alter any AWS resource, Google grant or write flag.

PR #30's exact implementation commit `1aabbdb` passed backend CI with **710 backend
tests, 12 offline EC2 checks and 19 summary-console checks**:
[CI run](https://github.com/bhowmikdham/Threadly/actions/runs/35054635897).
Current B10c verification is recorded in [its checkpoint](backend-execution/checkpoints/B10c.md).
Synthetic models/transports verify orchestration and failure handling, not live Haiku
quality or Google provider behavior. Whole-workflow API mappings and staging scenarios:
[testing map](workflow-testing-map.md); [machine-readable map](workflow-runtime-map.json).
