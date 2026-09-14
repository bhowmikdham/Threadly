# Threadly staging: AWS CloudShell launcher

This provisions a Docker **host**, not a working Threadly deployment. It creates a
separate CloudFormation stack without changing existing servers or deploying a
checkout of the application. Google OAuth, DNS, a Bedrock model and application
deployment still require configuration. There are no AWS credentials in this repo.

## Run

Sign into the intended AWS account, open **CloudShell** in the AWS console and
run the published, commit-pinned `cloudshell.sh` after verifying its SHA-256.
Alternatively, from a reviewed checkout in CloudShell:

```bash
bash infra/deploy/ec2/cloudshell.sh
```

CloudShell supplies an authenticated AWS CLI and Python. This is a Bash script;
do not paste it into the browser developer console or the EC2 user-data field.
It prints the account identity and explicitly targets Sydney by default, even
if the AWS console was previously showing a different region.

| Setting | Default | Purpose |
| --- | --- | --- |
| `THREADLY_REGION` | `ap-southeast-2` | Resource region |
| `THREADLY_STACK` | `threadly-staging` | One shared staging stack |
| `THREADLY_INSTANCE_TYPE` | `t3.large` | 2 vCPU, 8 GiB; also accepts `t3.medium` |
| `THREADLY_AUTO_STOP_HOURS` | `8` | Stop after 1–12 hours per timer activation/boot |
| `THREADLY_INSTANCE_PROFILE` | empty | Create a role/profile; otherwise reuse this profile name |

For an AWS Learner Lab that supplies `LabInstanceProfile`, use:

```bash
THREADLY_INSTANCE_PROFILE=LabInstanceProfile bash infra/deploy/ec2/cloudshell.sh
```

Use the actual **instance profile name**, not a role ARN. Reusing a profile avoids
creating IAM roles but does not change that profile's permissions. The lab may
restrict regions, instance types, Bedrock, CloudFormation or S3 independently.
The launcher cannot bypass those restrictions. It needs CloudFormation, EC2/VPC,
S3, public SSM parameter reads, image describes, and IAM pass-role permissions;
the default path also needs role/profile creation and policy attachment. The
person connecting needs Session Manager permissions. Use your authorised account
role; do not create root access keys.

## Created resources

| Resource | Configuration |
| --- | --- |
| Network | Dedicated VPC, public subnet, internet gateway and route table |
| EC2 | Canonical Ubuntu 24.04 amd64 AMI, verified publisher, `t3.large` |
| CPU credits | Standard mode; avoids Unlimited surplus-credit charges, but sustained load may throttle |
| Storage | Encrypted 30 GiB root + separate encrypted 50 GiB data disk; gp3 defaults |
| Address | Elastic IP retained across stop/start |
| Inbound traffic | TCP 443 only; no public SSH, PostgreSQL, Chroma or API port |
| Administration | SSM agent and Session Manager; IMDSv2 required |
| Runtime | Docker Engine and Compose plugin from Docker's signed Ubuntu repository |
| Docker volumes | Docker data-root on `/srv/threadly-data/docker`; bounded container logs |
| Backup destination | Private, encrypted, versioned S3 bucket; HTTPS-only access |
| Cost guard | Systemd timer stops EC2 after eight hours; resets on boot or timer restart |
| Secrets skeleton | Root-only `/srv/threadly-data/secrets/threadly.env`; generated app/database keys |

The new instance role allows SSM, invoking foundation models in the chosen region,
and reading/writing this stack's backup bucket. It does not permit infrastructure
administration. Cross-region inference profiles, application inference profiles
and Bedrock Flows need additional, specifically scoped permissions when selected.
Model access or marketplace prerequisites still need checking for the chosen model.
All containers capable of accessing instance metadata can potentially use the
instance role; hop limit 2 supports container credentials and is not per-container
IAM isolation. Keep the host restricted to trusted workloads.

No NAT gateway, load balancer, RDS, GPU, paid detailed metrics, Flow or model calls
are created. The S3 bucket is an **empty destination**, not an installed backup
job: current backup objects expire after 14 days and noncurrent versions after
7 days. Set up and restore-test backups before relying on them.

## Confirm the host is ready

CloudFormation completion means resources exist; cloud-init can still be
installing packages. Follow the SessionManagerUrl printed by the launcher, wait
for the agent to become available, then run on the server:

```bash
sudo cloud-init status --wait
sudo cat /opt/threadly/BOOTSTRAP_READY
sudo docker compose version
sudo systemctl list-timers threadly-autostop.timer
```

If the marker is missing or cloud-init reports an error, inspect
`/var/log/threadly-bootstrap.log` and `/var/log/cloud-init-output.log`. Check
outbound connectivity, instance-profile SSM permissions and package availability
if Session Manager never becomes available. The timer is a best-effort host
guard, not an AWS spending cap; a failure before cloud-init runs can prevent it.

The data disk is identified by its exact EBS volume serial before formatting.
Docker requires its mount, preventing normal startup onto an empty root-disk
directory if the data mount fails. Do not rerun user-data to recover an existing
deployment: diagnose the failed step first. Current Docker/containerd versions
may keep image layers under `/var/lib/containerd` on the root disk; monitor root
space separately from persistent named volumes.

## Daily start, stop and costs

The launcher prints commands with the actual instance ID and region. Use those
commands or the EC2 console **Stop instance** / **Start instance**. Do not use
Terminate for daily shutdown. Extend the current session on the server with:

```bash
sudo systemctl restart threadly-autostop.timer
```

The timer does not detect active developers, requests or worker jobs. Before
long integration tests or external-write workflows, plan a maintenance boundary;
extend it or explicitly disable it with `sudo systemctl disable --now
threadly-autostop.timer`, then re-enable with `sudo systemctl enable --now
threadly-autostop.timer` afterwards. Application reconciliation must still handle
interrupted Gmail/Calendar writes. This host guard does not implement that logic.

Stopped EC2 has no instance compute charge, but EBS, allocated public IPv4,
stored backups and other independently running services remain billable. Bedrock
requests cost extra whenever invoked. $110 of credits is not a hard spending
limit; eligibility and expiry depend on the credit programme. Configure account
budget alerts and monitor Billing separately. No budget alarm is installed here.

The launcher is create-only. Re-running it prints an existing Threadly stack and
does not update or start it. An unfinished/failed stack returns an error; inspect
its events instead of launching another name. A CloudShell disconnect does not
cancel CloudFormation creation. Keep the printed work directory and outputs.

## Application deployment handoff

1. Confirm the host checks above, and record stack name, instance ID, IP, volume
   ID and bucket name in the team's deployment record.
2. Choose a reviewed merged **application commit** and clone it into a dedicated
   directory under `/opt/threadly`. This infrastructure branch is not a promise
   that later application changes have been merged. Keep the Compose project name
   stable across deploys so it reuses the same named database volumes.
3. Edit the private environment file with `sudoedit`; supply the domain, Google
   OAuth client/secret/redirect URI and supported Bedrock model ID. Verify actual
   configuration names against that release. Keep secrets out of Git, shell
   history, pull requests and public deployment logs. No long-lived AWS keys are
   needed: use the instance role.
4. Point the domain's A record at the Elastic IP. Configure the application's
   HTTPS OAuth callback in Google and its allowed origins. Current Caddy exposes
   443 only; TLS-ALPN certificate issuance requires that DNS reaches this server.
5. Use that release's Compose file with the protected env file and its
   `assistant` worker profile. Build services, run its documented Alembic migration
   command, and start Caddy/API/PostgreSQL/Chroma/worker as required. Do not use the
   legacy Mac/Ollama setup or assume model files are configured by this launcher.
6. Verify HTTPS health, Google login, Bedrock inference, queue processing and a
   draft-only assistant request. Test approval enforcement and uncertain-write
   reconciliation before enabling real email/calendar writes.
7. Install scheduled off-instance PostgreSQL backups, document any Chroma rebuild
   strategy, and run a restore drill. Test a stop/start cycle with persistent data.

## Failure recovery and eventual removal

CloudFormation rolls back failed creation. The new data disk and bucket use
`RetainExceptOnCreate`, so a failed initial create can remove those unused new
resources. Successful stacks retain their data disk and bucket on later deletion.
Stack termination protection blocks accidental stack deletion; it does not block
someone directly terminating the EC2 instance. The root disk is deleted on
termination. Back up secrets and data before any replacement or teardown.

To deliberately remove a completed environment: take and verify a backup, record
the output IDs, stop application work and the EC2 instance so the data disk can
detach cleanly, disable **stack** termination protection
in CloudFormation, then delete the stack. Verify the deletion succeeds and the
Elastic IP is released. The retained data disk and S3 bucket continue costing
money until explicitly removed; a versioned bucket includes noncurrent versions
and delete markers. Do not remove retained data until its owner approves that
data loss. Do not delete retained disks merely to recover a failed deployment.

## Maintainer validation

Edit `render.py`, `user-data.sh`, `launcher-prefix.sh` and `launcher-suffix.sh`,
then regenerate committed `stack.json` and `cloudshell.sh`:

```bash
python3 infra/deploy/ec2/render.py
python3 -m unittest discover -s infra/deploy/ec2/tests -v
bash -n infra/deploy/ec2/cloudshell.sh
bash -n infra/deploy/ec2/user-data.sh
cfn-lint infra/deploy/ec2/stack.json
```

Tests mock AWS and never create cloud resources. Local checks do not prove live
account permissions, quotas, model availability or successful cloud-init. A first
live launch and the host checks remain required. The Ubuntu AMI is resolved and
recorded at launch; apt packages follow the official repository then available.
This is a staging bootstrap, not a fully pinned production image pipeline.

References: [CloudShell](https://docs.aws.amazon.com/cloudshell/latest/userguide/welcome.html),
[Canonical AMI discovery](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/),
[Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/),
[EC2 stop/start](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html),
[public IPv4 pricing](https://aws.amazon.com/vpc/pricing/).
