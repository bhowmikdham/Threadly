# Whole backend progress — 17 September 2026

Base: PR #36 merged at `06ca1a0b45afb50bd20dad9dfb6c02cc66603d0f` on
`codex/assistant-intent-routing`. This combined integration PR adds the remaining
bounded MVP backend paths. Frontend integration is still deferred. A merged PR,
prepared Flow, or passing mock test does not establish live acceptance.

## Formal package progress

B00–B20, 21 initial-release packages:

```text
[████▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒░░]
4 verified    15 implemented/partial/in review    2 planned
```

Source: `backend-execution/tasks.json`. **This is package status, not percent of
engineering effort or production readiness.** Seventeen packages remain formally
open, but that does not mean seventeen implementations remain to be written. B00
cross-team settlement and B20 pilot remain planned; several others await live/client
acceptance or intentionally narrower MVP scope. B21–B25 are later extensions.

## Backend implementation now available for testing

| Area | Implemented behavior | What remains |
|---|---|---|
| API/storage/lifecycle | Owned captures, idempotent tasks, leases, cancellation, events, migrations | Current EC2 deployment and staging recovery checks |
| Five intents | Summary, reply, compose, non-calendar plan, schedule and bounded Other | Current-model semantic/quality holdouts |
| Master coordinator | Reviewed whole-command interpretation, complete-clause/negation validation, finite graph compilation | Automatic routing and arbitrary graphs remain outside bounded MVP |
| Compound workflows | Summary→draft, lookup→draft, schedule→draft, summary→schedule→draft; saved separate outputs | Live integration and later frontend rendering |
| Plans/commitments | Evidence-backed proposed items, edits, dependencies, explicit selection, selected-only draft | Product acceptance; new-item insertion and general project management not installed |
| Calendar conversation | Deterministic slots, literal-time drafts, historical email-choice interpretation, exact-time recheck | Live Calendar comparison and model holdout; explicit fresh offer/selection remains required |
| Provider actions | Exact Gmail and Calendar previews/approvals; dedicated dispatch; uncertain-write reconciliation | Controlled real sends/events, then pilot sign-off |
| Google auth | Stored grants, reconnect/revocation boundaries, explicitly enrolled write scopes | Live account/callback/scope acceptance |
| Bedrock mappings | Existing three generation operations plus optional router/plan/meeting-response adapters; versioned assets | Published target/IAM checks and live quality before Flow activation |
| Retrieval | Captured lookup/rewrite, explicit local-mail search, literal entity candidates, selected commitments | Semantic/global retrieval is later scope |
| Reliability | Page-checkpointed background sync, atomic publication, worker heartbeats, owner queue status, intent/write switches | Real mailbox sizing, latency/cost and fault exercises |
| Retention/deployment | Conservative dry-run cleanup, migration guards, three-worker deployment, backup/rollback runbook | Host backup restore rehearsal, off-instance backup operations and broader erasure policy |

Full [workflow diagrams, API sequence and mappings](mvp-workflow-map.md).
Local evidence and limitations: [integration checkpoint](mvp-integration-checkpoint.md).

## Remaining work to declare the MVP accepted

1. Review and merge this integration PR after exact-head CI; deploy the merged revision
   on the existing EC2 host using the pinned script. Confirm migrations and all workers.
2. Configure/verify actual OAuth callbacks, test-account grants/revocation, Bedrock access
   and any published Flow registries. Native Bedrock generation can operate without
   publishing auxiliary visual Flows.
3. Run the [whole-intent acceptance matrix](testing/mvp-release-gate.md): live Haiku
   holdouts, real Calendar comparisons, approved controlled email/event actions,
   timeout/recovery checks, mailbox-size/queue measurements and host-backup restore.
4. Integrate the frontend when that work resumes: selected context, full proposal
   review, clarifications, step progress, artifact editing and separate action approvals.
   The API does not supply an implemented extension UI.
5. Run a small controlled pilot, fix recorded defects and obtain team sign-off. Keep
   writes disabled outside enrolled test users until acceptance is recorded.

The supplied BERT package was inspected: it is an email-content softmax classifier,
not a multi-label user-command router. It is not connected to the new master router.
A command-specific classifier requires labelled evaluation and a separate integration;
it is not needed to exercise the reviewed model coordinator.

## Explicit MVP boundaries

No automatic send/book from a command; no general agent tool loop; no arbitrary
workflow graph; no in-place free-text master-proposal editing. Missing inputs require
a corrected complete proposal. No Lambda read bridge: backend-owned prefetch is the
recorded [ADR 004](decisions/004-mvp-backend-owned-orchestration.md) design. No global
semantic retrieval, voice, proactive triggers, style learning, attachments, event
updates/recurrence or resource booking in this integration. Those are separate work.

## Deployment truth and expected completion

Last confirmed EC2 log in this task: PR #23, commit
`954b4926d5e0c4928ebecde06f2f67e918d5be06`. Later merges are not evidence of deployment.
This PR's migration head is `b17026e9a038` (base `f14026e9a036`). No AWS configuration,
Google grant, real email or real invitation was changed during local implementation.

Backend API acceptance can start as soon as the reviewed release is deployed and
provider configuration works. The completion date depends on live quality results,
Google access, frontend integration and pilot defects; Git history cannot establish a
reliable finish date for those gates. Track those outcomes rather than turning package
counts or agent runtime into a promise of completion.
