# Current EC2 deployment handoff

PR #23 is merged at **954b4926d5e0c4928ebecde06f2f67e918d5be06**. Deploy that
reviewed release while B02 action storage is under review. This document is a
command handoff; no new host deployment result has been received.

Use the **existing EC2 Session Manager terminal**, not CloudShell. The deployment
script requires `/opt/threadly/BOOTSTRAP_READY` on the application host. The known
host is `i-09a783f8a5b22df7f` in `ap-southeast-2`.

```bash
curl -fsSL 'https://raw.githubusercontent.com/bhowmikdham/Threadly/954b4926d5e0c4928ebecde06f2f67e918d5be06/infra/deploy/ec2/deploy-app.sh' -o /tmp/threadly-deploy.sh &&
printf '%s\n' '9204da17a8776e6d9134def735ab0cf54834443809bf47c71de1bbc3608442f5  /tmp/threadly-deploy.sh' | sha256sum -c - &&
sudo bash /tmp/threadly-deploy.sh 954b4926d5e0c4928ebecde06f2f67e918d5be06
```

The script checksum was verified against the merged Git object. It backs up the
existing database, stops API/worker, upgrades schema, restarts matching services
and checks health. It preserves environment files and volumes. Do not use volume
removal or manual destructive database cleanup for this deployment.

Expected success marker:

```text
DEPLOYMENT_READY commit=954b4926d5e0c4928ebecde06f2f67e918d5be06
```

Expected migration head: `c8291e4a6f03`. Preserve the actual script output, health
results and provider-configuration warnings. A healthy API/worker/database does not
prove Bedrock or Google connectivity. The last user-confirmed deployment was
`9b3b14e`; its output reported missing Bedrock model selection and Google OAuth
configuration. Live model/read tests remain pending until these are configured.

No automatic Git-to-server synchronization is established by this command. It
pins one release. B02's migration `d9302f5b7a14` is **not** included in this target;
use a newly verified merged commit after its review. Frontend work remains deferred.
