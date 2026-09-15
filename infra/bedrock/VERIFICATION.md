# Flow prototype launcher evidence — 15 September 2026

Scope: isolated CloudShell provisioning assets requested by the user, using
Claude Haiku 4.5. This is a preparatory subset of B16/B17; their backend acceptance
criteria and task statuses remain unchanged.

Verified locally:

- `python -m unittest discover -s infra/bedrock/tests -v`: **15 passed, 0 skipped**.
  Includes Bedrock API request shapes, inline CloudFormation size, role boundaries,
  profile destination/account checks, deterministic release identity, missing versus
  denied stacks, interrupted/repeated setup, template/Flow drift, rollback/timeouts,
  preparation failures, safe CLI argument handling, render-only zero AWS calls and
  partial manifests that cannot claim success.
- `ruff check infra/bedrock`: passed (ruff 0.16.7).
- `python infra/bedrock/cloudshell_flows.py --render-only`: rendered all six graphs,
  CloudFormation template and disabled manifest using a synthetic account; no AWS calls.
- `cfn-lint -t <rendered-template.json>`: passed (cfn-lint 1.56.3).
- API shape validation used botocore 1.43.93.
- `git diff --check`: passed.

Nine synthetic prompt replay fixtures are versioned in `fixtures.json`, with source
scope, ambiguous time, insufficient slot count, draft-versus-send and source injection
rubrics. They are **not live model evaluation results**. No claim of model adherence
or source accuracy is supported by the provisioning tests alone.

Not run: AWS stack creation, actual PrepareFlow, billed InvokeFlow/Haiku evaluation,
Google integration, EC2 deployment or backend regression/database tests (runtime and
schema unchanged). The added CI workflow repeats offline checks; a committed workflow
is not proof of a completed hosted CI run.

Next: user runs the checksum-pinned CloudShell launcher and supplies any setup error
or preparation result. AI/backend then evaluate synthetic cases and implement B16/B17
contracts/registry/read bridge/validators, plus dependent Calendar and action services,
before promoting immutable versions and enabling application invocation.
