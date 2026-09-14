# Validation record — 14 September 2026

Scope: standalone staging infrastructure launcher and Docker host bootstrap.
Application features and other agents' branches are outside this change.

Passed locally:

- `python3 -m unittest discover -s infra/deploy/ec2/tests -v`: 7 tests passed.
  Includes happy-path creation, optional existing profile, read-only rerun,
  permission/AMI/profile/stack-state failures, invalid input and failed waiter.
  All AWS calls use a fake executable; generated artifacts match their sources.
- `cfn-lint infra/deploy/ec2/stack.json`: no findings.
- `bash -n` for the generated launcher and user-data: passed.
- Ruff checks for the generator and test module: passed.
- `git diff --check`: passed.

Not performed: live AWS create, cloud-init execution on Ubuntu, SSM connection,
application deployment, model invocation, Google OAuth or backup/restore tests.
Those require the target account and the deployment checks in README.md.
No resources were provisioned or cloud credentials stored by local validation.
