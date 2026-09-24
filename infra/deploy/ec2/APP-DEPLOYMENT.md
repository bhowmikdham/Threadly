# Deploy the API, PostgreSQL, Chroma and workers without a domain

Current source policy: [on-demand Gmail](../../../docs/on-demand-gmail.md). Set
`GMAIL_SOURCE_MODE=on_demand` and `MAILBOX_BACKGROUND_SYNC_ENABLED=false` in the
protected environment before deployment. The retired sync worker is stopped and
is not restarted. Gmail bodies are not imported into PostgreSQL or Chroma.

The host bootstrap does not clone Git or deploy containers. `deploy-app.sh`
clones the public Threadly repository into `/opt/threadly/releases/<commit>`,
checks out the exact requested commit, builds one backend image and deploys it.
No GitHub token or SSH key is needed for public repository reads. GitHub pushes
do **not** automatically change the running server.

Run on the bootstrapped server through Session Manager, with a reviewed full
commit containing these deployment files:

```bash
sudo bash deploy-app.sh FULL_40_CHARACTER_COMMIT
```

Download the script from the same immutable commit and verify its checksum
before execution. Deployment retains the existing generated secrets file at
`/srv/threadly-data/secrets/threadly.env` (root:600). The script never sources it
as shell code or prints it. APP_ENV stays `prod`; blank Google/model settings
are reported as pending, not silently replaced with fake functionality.

## Deployment order and evidence

1. Acquire a deployment lock, verify data disk and protected environment file.
2. Clone/check out the exact commit; refuse dirty release directories.
3. Validate Compose, build the API image, validate application settings without
   provider calls. The worker uses the same image.
4. Start PostgreSQL, stop any old API/worker, create a compressed database dump,
   then apply Alembic migrations. A failed build does not stop the old app; a
   failed migration leaves the app stopped for inspection and data intact.
5. Start Chroma, API and worker; wait for API readiness (database + Chroma),
   check HTTP health and verify the worker container is running.
6. Record the commit and actual image IDs in `/srv/threadly-data/deployment/`;
   point `/opt/threadly/current` to the successful checkout.

The worker's running state is not proof that a model job succeeds. Test that
separately after configuring Bedrock. No synthetic user, email, invite or model
invocation is created by deployment. Existing queued jobs may be processed when
the worker starts; deploy only to the intended staging database.

PostgreSQL and Chroma tags follow the repository baseline (`postgres:16`,
`chromadb/chroma:latest`). Missing images are pulled; subsequent releases reuse
local images, and their resolved IDs are recorded. Backend dependencies still
use the existing version ranges. This pins application source, not the complete
dependency supply chain. Dependency updates and digest pinning remain release
engineering work; do not prune images required for rollback casually.

## Access without DNS

The API listens on **127.0.0.1:8000 on the EC2 host**. It is not available at the
public IP from a laptop. Caddy is not started and no security-group rules are
changed. PostgreSQL and Chroma have no host port mappings.

On the server:

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
```

On a developer laptop with AWS CLI credentials and the Session Manager plugin:

```bash
aws ssm start-session --region ap-southeast-2 \
  --target i-09a783f8a5b22df7f \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
```

Keep the tunnel open and use `http://localhost:8000`. The browser-based SSM shell
does not itself forward a port onto your laptop. AWS CLI authentication and plugin
installation are separate prerequisites; do not store root access keys for this.
See [AWS port-forwarding instructions](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html#sessions-start-port-forwarding).

Google OAuth needs a registered callback and the application's matching configuration.
Use the [domain-free local test client](../../../docs/google-local-testing.md) with the
explicit, exact loopback exception; HTTP localhost is rejected by default. A public IP callback is not accepted
by Google's web OAuth rules. Production CORS in the merged baseline does not
yet allow an extension origin; browser/extension integration is a later setup
step, not solved by exposing a port. The domain/TLS deployment will need a
reviewed Compose/proxy transition without changing the `threadly` project name
or its named volumes.

## Redeploy, logs and recovery

Each deployment is an explicit commit selection and script execution, not `git
pull` on a mutable working tree. Keep release directories clean. To inspect the
current deployment on the server:

```bash
sudo cat /srv/threadly-data/deployment/current-commit
sudo docker ps --filter label=com.docker.compose.project=threadly
sudo docker logs --tail 50 threadly-assistant-worker-1
```

Logs can contain operational/user data; inspect locally and redact before sharing.
The existing eight-hour auto-stop timer remains active. Stop/start preserves named
volumes and `restart: unless-stopped` services start again after Docker starts,
provided they were not manually stopped. Plan interruptions around active work.

The predeployment dump is a **local recovery aid on the same data disk**, not an
off-instance backup or a restore-tested backup programme. It includes staging
database contents and must remain private. Copy required backups to the prepared
private bucket and test restore separately; no automatic retention job is installed
for these local dumps. Monitor disk usage. The script does not delete volumes or
automatically downgrade migrations. After a failed migration or incompatible
release, inspect schema and backup before selecting a compatible code rollback.


## B06 action-worker lifecycle (historical; superseded for the MVP)

The B06 staging stack adds `action-worker` using the same release image as the API.
Deployment stops API, assistant-worker and action-worker before backup/migration,
then starts matching versions and checks both workers are running. A process-running
check is not proof of Gmail/Bedrock operation; live tests remain separate. Defaults
`EMAIL_WRITES_ENABLED=false` and `EMAIL_RECONCILIATION_ENABLED=false` keep provider
activity off in the action worker. Preflight rejects an attempted write flag while
the compiled controlled-account gate remains closed. No sends are enabled by deploy.

To roll back to a pre-B06 script, stop the action-worker using the **current**
release Compose configuration first; older scripts do not stop that service.
Preserve unknown attempts and all action history. No destructive downgrade or resend
is part of rollback. [Recovery runbook](../../../docs/email-actions.md).

For the combined MVP, use [MVP-ROLLOUT.md](MVP-ROLLOUT.md). It keeps mailbox sync
retired, checks the assistant/action workers and documents explicit per-user pilot
gates plus the contextual-conversation feature gate; the historical B06 compiled-gate
description above no longer describes current pilot enablement.
