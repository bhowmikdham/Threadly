# Combined MVP: merge, deploy, then validate

This runbook does not enable writes or run billed model tests. Deploy only the reviewed
merged commit. The current PR is based on PR #36; the last deployment confirmed in this
conversation was PR #23, so do not assume EC2 tracks GitHub automatically.

## Before deployment

- Confirm exact-head CI and merge the integration PR into `codex/assistant-intent-routing`.
- Record the full resulting merge SHA and the SHA-256 of that commit's
  `infra/deploy/ec2/deploy-app.sh`. Download from that pinned raw GitHub URL, verify
  checksum, then run with `sudo bash /tmp/threadly-deploy.sh <full-merge-sha>` **inside
  the EC2 Session Manager shell**. AWS CloudShell is not the bootstrapped EC2 host.
  The assistant will supply the concrete pinned command after merge; do not paste
  unfilled placeholders or a moving branch URL.
- Keep the protected environment at `/srv/threadly-data/secrets/threadly.env`, root:600.
  Preserve existing secrets, OAuth settings and current model/profile selection.
  Set `EMAIL_WRITES_ENABLED=false`, `CALENDAR_WRITES_ENABLED=false`, and leave
  `WRITE_PILOT_USER_IDS` empty for initial deployment. No external write is needed
  for API startup. Reconciliation flags default false and may be enabled separately
  for known unknown-outcome records.
- New optional settings: `ASSISTANT_AUXILIARY_WORKFLOW_MANIFEST` (empty uses configured
  native generation), `ASSISTANT_DISABLED_INTENTS` (empty),
  `MAILBOX_BACKGROUND_SYNC_ENABLED=true`, `MAILBOX_SYNC_MAX_MESSAGES=5000`.
  Keep API and all workers on the same configuration and application release.

The script stops API and all three workers, creates a pre-migration PostgreSQL dump,
applies Alembic through `b17026e9a038`, and starts API, assistant-worker, action-worker
and sync-worker from the same image. It checks API readiness and worker heartbeats.
The host remains domain-free, API bound to loopback, with SSM forwarding for access.
It does not modify security groups, register callbacks, grant model access, prepare
new Flows, enable writes or remove the eight-hour host auto-stop timer.

## Confirm on EC2

```bash
sudo cat /srv/threadly-data/deployment/current-commit
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
sudo docker ps --filter label=com.docker.compose.project=threadly
sudo systemctl list-timers --all --no-pager threadly-autostop.timer
```

Record commit, migration head and all container health states. Do not paste tokens,
OAuth codes, environment files, private logs or database dumps into GitHub. With an
existing authenticated test session, check `/assistant/capabilities`,
`/assistant/workflows` and `/assistant/operational-status` before model/provider tests.
Use the SSM port-forwarding procedure in [APP-DEPLOYMENT.md](APP-DEPLOYMENT.md).

## Optional auxiliary visual Flows

From the release's backend environment, render without contacting AWS:

```bash
python -m app.workflows.release --auxiliary --profile-arn '<verified Australian Haiku profile ARN>' --output /tmp/threadly-aux-flows
python -m app.workflows.mvp_assets --check --output fixtures/mvp/prompts-v1.json
```

Review and deploy the rendered CloudFormation stack, then prepare, publish numbered
versions and immutable reviewed aliases using the same procedure as the existing
runtime registry. `--auxiliary --targets <published-targets.json> --output <new-dir>`
validates the three entries and emits a candidate registry plus scoped caller policy.
Run a separately authorized live quality gate before configuring it. The six earlier
prototype console Flows have different contracts and cannot be substituted by name.
No new Flow is necessary to start the API using its configured native Bedrock adapter.

## Controlled writes, after acceptance authorization

A tester must be explicitly enrolled through `WRITE_PILOT_USER_IDS` (local positive
user IDs). Grant the requested Gmail send / Calendar event scope through reconnect;
requested scopes alone never count as granted. Set only the approved write flag and
its matching reconciliation flag. Preflight refuses enabled writes with an empty
allowlist or disabled recovery reads. Every action still requires exact payload/hash
approval. Do not enroll all users, auto-approve artifacts or submit real messages/events
as a deployment smoke test. Disabling writes prevents new dispatch; it cannot undo an
already dispatched provider request. Keep recovery reads available for unknown outcomes.

## Failure and rollback

1. If deployment fails, inspect the failing step. The script preserves data volumes and
   does not change the recorded current release until all startup checks pass. Some
   containers/schema may already have changed; the current-commit file alone is not
   proof of a complete rollback.
2. Pause all writes and generation intents. Stop workers with the **current** Compose
   file before attempting an older release; it knows about the sync-worker.
3. Preserve the existing database and action records. Migrations refuse downgrade while
   new workflow/edited-plan data or active sync jobs exist. Prefer a forward fix or
   schema-compatible code rollback. Never delete new tasks merely to force downgrade.
4. Restoring a predeploy dump is a deliberate recovery decision: it loses later state.
   Provider writes cannot be rolled back by database restore. Preserve/reconcile
   dispatch evidence before restoring; never restart send workers on a database that
   forgot attempted sends.
5. Do not remove named volumes, prune rollback images or overwrite protected secrets.
   The local dump shares the data disk and is not an off-instance disaster backup.

## Backup/restore acceptance

Use a private copy of an identified dump. Verify gzip integrity, restore into a **new,
empty isolated PostgreSQL database**, then check Alembic head, owned mailbox counts,
representative task/artifact/action hashes and unknown-attempt records. Keep all workers
and provider credentials detached from the restored database. Record source dump hash,
release, command outcome and comparisons; destroy only the explicitly created rehearsal
database afterwards. Rehearse the actual deployed dump before pilot acceptance. An
upgrade/downgrade test is not evidence that that host's backup was restored.

Run conservative retention dry-run first. Keep enough private disk space for dump +
restore + staged backfill; do not claim capacity or cost duration without measurements.
