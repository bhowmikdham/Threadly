# Current EC2 deployment handoff

PR #23 deployment is confirmed by user-provided host output: commit
`954b4926d5e0c4928ebecde06f2f67e918d5be06`, migration `c8291e4a6f03`, healthy API,
PostgreSQL, Chroma and worker. Its backup was recorded at
`predeploy-20260915T141644Z-954b4926d5e0c4928ebecde06f2f67e918d5be06.sql.gz`.
Bedrock model selection and Google OAuth remain explicitly pending.

PR #28 is merged at **fd0eaeac94422832d8d644e342edfd36c12945bd**. The command
below upgrades to the reviewed B05 worker code, including auth, action storage,
exact previews and approval/stop decisions. Deployment is unconfirmed. This merged
release does not start the action worker in staging. B06 recovery and staging worker
wiring are on the current feature branch and excluded; sending remains disabled.
OAuth exchange now requires state/PKCE: see [client compatibility](google-capabilities.md).

Use the **existing EC2 Session Manager terminal**, not CloudShell. The deployment
script requires `/opt/threadly/BOOTSTRAP_READY` on the application host. The known
host is `i-09a783f8a5b22df7f` in `ap-southeast-2`.

```bash
curl -fsSL 'https://raw.githubusercontent.com/bhowmikdham/Threadly/fd0eaeac94422832d8d644e342edfd36c12945bd/infra/deploy/ec2/deploy-app.sh' -o /tmp/threadly-deploy.sh &&
printf '%s\n' '9204da17a8776e6d9134def735ab0cf54834443809bf47c71de1bbc3608442f5  /tmp/threadly-deploy.sh' | sha256sum -c - &&
sudo bash /tmp/threadly-deploy.sh fd0eaeac94422832d8d644e342edfd36c12945bd
```

The script checksum was verified against the merged Git object. It backs up the
existing database, stops API/worker, upgrades schema, restarts matching services
and checks health. It preserves environment files and volumes. Do not use volume
removal or manual destructive database cleanup for this deployment.

Expected success marker:

```text
DEPLOYMENT_READY commit=fd0eaeac94422832d8d644e342edfd36c12945bd
```

Expected migration head: `a0426e9bc731`. Preserve the actual script output, health
results and provider-configuration warnings. A healthy API/worker/database does not
prove Bedrock or Google connectivity. The last user-confirmed deployment is PR #23; its output still reports missing
Bedrock model selection and Google OAuth configuration. Live model/read tests remain pending until these are configured.

No automatic Git-to-server synchronization is established by this command. It
pins one release. B02 action storage, B01 Google metadata/state and B04 decision receipt migrations are included. Guarded downgrade
refuses if action history exists; do not remove history to force rollback. No sender
is enabled or booking executor installed. B05 worker code is included but staging
startup/recovery wiring is deferred to B06. Frontend work remains deferred.
