"""Exercise the real Bash launcher with a fake AWS executable; no network calls."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_AWS = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
case = os.environ.get("FAKE_CASE", "success")
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
def output(value):
    print(json.dumps(value))
def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)
def value(flag):
    return args[args.index(flag) + 1]
command = tuple(args[:2])
if command == ("sts", "get-caller-identity"):
    output({"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/test"})
elif command == ("cloudformation", "describe-stacks"):
    outputs = [{"OutputKey": "InstanceId", "OutputValue": "i-0123456789abcdef0"},
               {"OutputKey": "PublicIp", "OutputValue": "192.0.2.1"}]
    if "--query" in args:
        output(outputs)
    elif case in {"existing", "existing_failed", "foreign"}:
        output({"Stacks": [{"StackStatus": "ROLLBACK_COMPLETE" if case == "existing_failed" else "CREATE_COMPLETE",
            "Tags": [{"Key": "Project", "Value": "AnotherProject" if case == "foreign" else "Threadly"}],
            "Outputs": outputs}]})
    elif case == "denied":
        fail("An error occurred (AccessDenied) when calling the DescribeStacks operation")
    else:
        fail("An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id " + value("--stack-name") + " does not exist")
elif command == ("ssm", "get-parameter"):
    print("ami-0123456789abcdef0")
elif command == ("ec2", "describe-images"):
    output({"Images": [{"OwnerId": "111111111111" if case == "bad_ami" else "099720109477",
        "Architecture": "x86_64", "State": "available", "VirtualizationType": "hvm",
        "RootDeviceType": "ebs", "RootDeviceName": "/dev/sda1"}]})
elif command == ("iam", "get-instance-profile"):
    if case == "bad_profile":
        fail("NoSuchEntity: instance profile missing")
    print("arn:aws:iam::123456789012:instance-profile/" + value("--instance-profile-name"))
elif command == ("cloudformation", "validate-template"):
    body = pathlib.Path(value("--template-body").removeprefix("file://")).read_text()
    assert len(body.encode()) < 51200
    assert json.loads(body)["Resources"]["Instance"]["Type"] == "AWS::EC2::Instance"
    print("Template valid")
elif command == ("cloudformation", "create-stack"):
    params = json.loads(pathlib.Path(value("--parameters").removeprefix("file://")).read_text())
    pathlib.Path(os.environ["FAKE_PARAMS"]).write_text(json.dumps(params))
    assert "--enable-termination-protection" in args
    assert value("--on-failure") == "ROLLBACK"
    output({"StackId": "arn:aws:cloudformation:ap-southeast-2:123456789012:stack/threadly-staging/test"})
elif command == ("cloudformation", "wait"):
    if case == "wait_failed":
        fail("Waiter StackCreateComplete failed")
elif command == ("cloudformation", "describe-stack-events"):
    print("Instance CREATE_FAILED: test capacity failure")
else:
    fail("Unexpected AWS call: " + repr(args))
"""


class LauncherTests(unittest.TestCase):
    def run_launcher(self, case="success", **overrides):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            aws = folder / "aws"
            aws.write_text(FAKE_AWS)
            aws.chmod(0o755)
            log = folder / "calls.jsonl"
            params = folder / "params.json"
            env = {k: v for k, v in os.environ.items() if not k.startswith("THREADLY_")}
            env.update(
                HOME=temporary,
                PATH=temporary + os.pathsep + env["PATH"],
                FAKE_CASE=case,
                FAKE_LOG=str(log),
                FAKE_PARAMS=str(params),
            )
            env.update(overrides)
            result = subprocess.run(
                ["bash", str(ROOT / "cloudshell.sh")],
                env=env,
                capture_output=True,
                check=False,
                text=True,
                timeout=20,
            )
            calls = (
                [json.loads(line) for line in log.read_text().splitlines()]
                if log.exists()
                else []
            )
            parameters = json.loads(params.read_text()) if params.exists() else []
            return (
                result,
                calls,
                {p["ParameterKey"]: p["ParameterValue"] for p in parameters},
            )

    def test_success_creates_once_and_prints_concrete_controls(self):
        result, calls, params = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sum(c[:2] == ["cloudformation", "create-stack"] for c in calls), 1
        )
        self.assertEqual(params["InstanceType"], "t3.large")
        self.assertEqual(params["ExistingInstanceProfile"], "")
        self.assertEqual(params["AutoStopHours"], "8")
        self.assertIn(
            "start-instances --region ap-southeast-2 --instance-ids i-0123456789abcdef0",
            result.stdout,
        )
        self.assertIn("application is NOT deployed", result.stdout)

    def test_reuse_profile_and_custom_session(self):
        result, calls, params = self.run_launcher(
            THREADLY_INSTANCE_PROFILE="LabInstanceProfile",
            THREADLY_AUTO_STOP_HOURS="4",
            THREADLY_INSTANCE_TYPE="t3.medium",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(params["ExistingInstanceProfile"], "LabInstanceProfile")
        self.assertEqual(params["AutoStopHours"], "4")
        self.assertTrue(any(c[:2] == ["iam", "get-instance-profile"] for c in calls))

    def test_existing_stack_is_read_only(self):
        result, calls, _ = self.run_launcher("existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn("No changes made", result.stdout)

    def test_failures_never_create_a_stack(self):
        for case in ["denied", "existing_failed", "foreign", "bad_ami", "bad_profile"]:
            with self.subTest(case=case):
                result, calls, _ = self.run_launcher(
                    case, THREADLY_INSTANCE_PROFILE="LabInstanceProfile"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    any(c[:2] == ["cloudformation", "create-stack"] for c in calls)
                )

    def test_reject_invalid_input_before_aws_calls(self):
        for overrides in [
            {"THREADLY_STACK": "bad;command"},
            {"THREADLY_AUTO_STOP_HOURS": "0"},
            {"THREADLY_AUTO_STOP_HOURS": "13"},
            {"THREADLY_INSTANCE_TYPE": "p5.48xlarge"},
            {"THREADLY_INSTANCE_PROFILE": "$(touch unwanted)"},
        ]:
            with self.subTest(overrides=overrides):
                result, calls, _ = self.run_launcher(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_wait_failure_reports_events_without_retrying_create(self):
        result, calls, _ = self.run_launcher("wait_failed")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test capacity failure", result.stdout)
        self.assertNotIn("Infrastructure created.", result.stdout)
        self.assertEqual(
            sum(c[:2] == ["cloudformation", "create-stack"] for c in calls), 1
        )

    def test_committed_artifacts_match_sources(self):
        spec = importlib.util.spec_from_file_location("ec2_render", ROOT / "render.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        body = json.dumps(module.template(), indent=2) + "\n"
        self.assertEqual((ROOT / "stack.json").read_text(), body)
        self.assertEqual(
            (ROOT / "cloudshell.sh").read_text(),
            (ROOT / "launcher-prefix.sh").read_text()
            + body
            + (ROOT / "launcher-suffix.sh").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
