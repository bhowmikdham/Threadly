# On-demand Gmail architecture correction

## Scope and identity

- User-directed correction after the live sync test: do not replicate the mailbox.
- Branch `codex/on-demand-gmail`; base merged PR #38,
  `e2a1773bc92512514b207e8ce11cebaee6b5f79d`.
- Runtime contract and diagram: [on-demand Gmail](../../on-demand-gmail.md).
- Model/prompts/Bedrock targets are unchanged. No schema migration.
- Frontend integration, real writes and broader model-quality acceptance remain separate.

## Delivered behavior

Bounded live thread/search reads; request/attempt-local email memory; reference-only
captures; independent worker hydration; owned account/fingerprint checks for summaries,
plans, replies, typed reads, compound scheduling and exact action sources. Cancel/reject
and meeting close remain usable without Gmail. No mailbox fallback, original Message
rows or source embedding writes. Generated artifacts and exact outgoing approval payloads
remain durable by design.

`/sync` and sync-job routes return 410. Background claims and the old incremental sync
entrypoint are also gated. Compose profiles retire sync-worker; deployment stops any old
worker and starts only API/assistant/action workers. Preflight rejects replication mode
or an enabled old sync flag. Existing settings and provider write gates are preserved.

## Actual evidence

- Backend suite from `backend/`, Python 3.12, isolated PostgreSQL 16 with required-DB
  flag: **1,091 passed**, two dependency deprecation warnings, 332.49 seconds.
- Final action/Calendar/negotiation plus on-demand check: **94 passed**, one new test
  fixture omitted required `continuation`; fixed to match the existing request contract.
  Final on-demand rerun: **17 passed**, two dependency warnings, 10.49 seconds.
- EC2 deployment tests: **16 passed, 18 subtests passed**, 6.39 seconds; includes rejecting
  legacy source mode and enabled sync flags. Ruff and shell syntax checked.
- Versioned MVP prompt assets match runtime; no model/provider call from this check.
- Initial repository-root pytest invocation missed backend asyncio configuration and was
  discarded; the authoritative full run uses the same backend working directory as CI.
- Real Google/model requests are mocked in automated backend tests. Source tests assert
  no open DB transactions during Gmail HTTP, no stored messages, owner isolation,
  fingerprint invalidation, bounded cursors and offline cancellation/close.

## Live containment and cleanup

Verified the authorized staging instance with AWS profile `threadly` and SSM. Audit
command `6c609321-1c41-4efa-b66c-b8bc52a90d61` found sync-worker stopped with restart=no,
300 staging rows for the one test job, and zero published messages, threads, contexts or
tasks. Cleanup command `2677bb3d-983b-415b-8bc0-60ef3b01c809` rechecked the stopped worker,
queued/unleased exact test job and absence of published owner data, then removed only
that job/staging in a transaction. Verification: staging 0, messages 0, Google connections 1.
No claims of forensic erasure from WAL/disk/backups. No provider writes performed.

## Rollout and remaining gates

Deployment of this correction is pending at this checkpoint. Set protected environment
`GMAIL_SOURCE_MODE=on_demand`, `MAILBOX_BACKGROUND_SYNC_ENABLED=false`, keep external
writes disabled, deploy the tested pinned commit, then check running image/readiness,
retired sync, live bounded Gmail and zero persisted source content. Record the exact
release and counts in PR deployment evidence. Do not run fixtures against EC2.

Historical captures cannot be used in the new default mode; recapture them. Older images
cannot understand reference-only captures. Do not restore sync or roll back blindly.
No mailbox-wide classification or semantic search is provided by this bounded change.
