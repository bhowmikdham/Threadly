# Current EC2 deployment handoff

PR #23 deployment is confirmed by user-provided host output: commit
`954b4926d5e0c4928ebecde06f2f67e918d5be06`, migration `c8291e4a6f03`, healthy API,
PostgreSQL, Chroma and worker. Its backup was recorded at
`predeploy-20260915T141644Z-954b4926d5e0c4928ebecde06f2f67e918d5be06.sql.gz`.
Bedrock model selection and Google OAuth remain explicitly pending.

PR #24 is now merged at **96648749c75f9f7c6445e175345b2b997e7201eb**. The command
below upgrades to that reviewed action-storage release. Its deployment is not yet
confirmed. The B01 auth changes in the current feature branch are excluded.

Use the **existing EC2 Session Manager terminal**, not CloudShell. The deployment
script requires `/opt/threadly/BOOTSTRAP_READY` on the application host. The known
host is `i-09a783f8a5b22df7f` in `ap-southeast-2`.

```bash
curl -fsSL 'https://raw.githubusercontent.com/bhowmikdham/Threadly/96648749c75f9f7c6445e175345b2b997e7201eb/infra/deploy/ec2/deploy-app.sh' -o /tmp/threadly-deploy.sh &&
printf '%s\n' '9204da17a8776e6d9134def735ab0cf54834443809bf47c71de1bbc3608442f5  /tmp/threadly-deploy.sh' | sha256sum -c - &&
sudo bash /tmp/threadly-deploy.sh 96648749c75f9f7c6445e175345b2b997e7201eb
```

The script checksum was verified against the merged Git object. It backs up the
existing database, stops API/worker, upgrades schema, restarts matching services
and checks health. It preserves environment files and volumes. Do not use volume
removal or manual destructive database cleanup for this deployment.

Expected success marker:

```text
DEPLOYMENT_READY commit=96648749c75f9f7c6445e175345b2b997e7201eb
```

Expected migration head: `d9302f5b7a14`. Preserve the actual script output, health
results and provider-configuration warnings. A healthy API/worker/database does not
prove Bedrock or Google connectivity. The last user-confirmed deployment is PR #23; its output still reports missing
Bedrock model selection and Google OAuth configuration. Live model/read tests remain pending until these are configured.

No automatic Git-to-server synchronization is established by this command. It
pins one release. B02 action-storage migration `d9302f5b7a14` is included. Its guarded downgrade
refuses if action history exists; do not remove history to force rollback. No sender
or booking executor is installed. Frontend work remains deferred.
