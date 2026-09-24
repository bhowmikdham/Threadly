# Current EC2 deployment handoff

Repository history is not proof of the release currently running on EC2. The source of
truth is the protected host record at `/srv/threadly-data/deployment/current-commit`,
the live Alembic head and the running container image IDs. Older PR-specific commands
and checksums have been removed because using them would deliberately deploy stale code.

The known staging host identifier is `i-09a783f8a5b22df7f` in `ap-southeast-2`.
Connect through Session Manager, then record the current state before changing it:

```bash
sudo cat /srv/threadly-data/deployment/current-commit
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
sudo docker ps --filter label=com.docker.compose.project=threadly
sudo docker exec threadly-api-1 alembic current
```

Do not paste environment files, tokens, OAuth codes, private logs or database dumps into
GitHub. Healthy containers prove local process/dependency readiness only. They do not prove
Bedrock access, Google OAuth scopes, model quality, Gmail/Calendar behavior or write recovery.

## Next deployment

Deploy only a reviewed, merged 40-character commit whose exact-head CI passed. From that
immutable commit, download `infra/deploy/ec2/deploy-app.sh`, verify its separately recorded
SHA-256 and run it inside the EC2 Session Manager shell:

```text
sudo bash /tmp/threadly-deploy.sh <reviewed-full-commit>
```

Follow [the combined rollout](../infra/deploy/ec2/MVP-ROLLOUT.md) for the protected
environment gates and [the application deployment runbook](../infra/deploy/ec2/APP-DEPLOYMENT.md)
for backup, migration, health and rollback steps. The current schema head for the
contextual-conversation release is `c23026e9a039`; always confirm the deployed commit's
actual Alembic head rather than copying this value into a command.

Initial contextual-chat rollout keeps `EMAIL_WRITES_ENABLED=false` and
`CALENDAR_WRITES_ENABLED=false`. Set `CONVERSATION_ENABLED=true` only with the exact reviewed
Haiku inference-profile ARN and after the separate account logging/content-boundary audit;
the acknowledgement flag is not a technical verification. Run the live conversation
evaluation and save a sanitized receipt only after those checks. No automatic Git-to-server
synchronization is established: merging or pushing a branch never changes EC2.
