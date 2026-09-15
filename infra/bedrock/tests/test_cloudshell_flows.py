"""Offline provisioning tests. They do not establish live model quality/access."""

import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.session import Session
from botocore.validate import validate_parameters

MODULE_PATH = Path(__file__).resolve().parents[1] / "cloudshell_flows.py"
SPEC = importlib.util.spec_from_file_location("cloudshell_flows", MODULE_PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
ACCOUNT = "123456789012"
ARN = f"arn:aws:bedrock:{m.REGION}:{ACCOUNT}:inference-profile/{m.PROFILE}"
MODELS = [
    f"arn:aws:bedrock:{region}::foundation-model/{m.MODEL}"
    for region in ("ap-southeast-2", "ap-southeast-4")
]
ROLE = f"arn:aws:iam::{ACCOUNT}:role/test"
FLOW = f"arn:aws:bedrock:{m.REGION}:{ACCOUNT}:flow/ABCDEFGHIJ"


class FakeAws:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, service, action, payload=None):
        self.calls.append((service, action, payload))
        expected, reply = self.replies.pop(0)
        if action != expected:
            raise AssertionError(f"Expected {expected}, received {action}")
        if isinstance(reply, Exception):
            raise reply
        return copy.deepcopy(reply)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.template, self.graphs, self.hash, self.tags = m.build_template(
            ARN, MODELS, ACCOUNT
        )

    def stack(self, status="CREATE_COMPLETE", tags=None):
        return {
            "Stacks": [
                {
                    "StackStatus": status,
                    "Tags": [
                        {"Key": key, "Value": value}
                        for key, value in (tags or self.tags).items()
                    ],
                    "Outputs": [{"OutputKey": "RoleArn", "OutputValue": ROLE}],
                }
            ]
        }

    def flow(self, status="Prepared", graph=None):
        return {
            "id": "ABCDEFGHIJ",
            "status": status,
            "definition": graph or self.graphs["summary"],
            "executionRoleArn": ROLE,
        }

    def test_all_six_graphs_validate_against_aws_sdk(self):
        service = Session().get_service_model("bedrock-agent")
        for op, graph in self.graphs.items():
            validate_parameters(
                {
                    "name": "threadly-test-" + op,
                    "definition": graph,
                    "executionRoleArn": ROLE,
                },
                service.operation_model("CreateFlow").input_shape,
            )
        self.assertEqual(
            set(self.graphs),
            {"summary", "plan", "schedule", "reply", "compose", "other"},
        )

    def test_cloudformation_create_request_and_inline_size(self):
        request = {
            "StackName": "threadly-flow-prototypes-" + self.hash[:12],
            "TemplateBody": m.canonical(self.template),
            "Capabilities": ["CAPABILITY_IAM"],
            "TimeoutInMinutes": 20,
            "ClientRequestToken": self.hash,
        }
        validate_parameters(
            request,
            Session()
            .get_service_model("cloudformation")
            .operation_model("CreateStack")
            .input_shape,
        )
        self.assertLess(len(request["TemplateBody"].encode()), 51200)

    def test_only_flow_and_restricted_model_role_resources(self):
        resources = self.template["Resources"]
        self.assertEqual(len(resources), 7)
        self.assertEqual(
            {r["Type"] for r in resources.values()},
            {"AWS::IAM::Role", "AWS::Bedrock::Flow"},
        )
        role = resources["FlowRole"]["Properties"]
        statements = role["Policies"][0]["PolicyDocument"]["Statement"]
        self.assertEqual(statements[0]["Resource"], [ARN])
        self.assertEqual(statements[1]["Resource"], MODELS)
        self.assertEqual(
            statements[1]["Condition"]["StringEquals"]["bedrock:InferenceProfileArn"],
            ARN,
        )
        for statement in statements:
            self.assertEqual(
                statement["Action"],
                ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            )
        self.assertEqual(
            role["AssumeRolePolicyDocument"]["Statement"][0]["Condition"][
                "StringEquals"
            ],
            {"aws:SourceAccount": ACCOUNT},
        )
        self.assertFalse(self.template["Metadata"]["BackendEnabled"])

    def test_release_hash_changes_for_prompt_or_profile_destinations(self):
        self.assertEqual(m.build_template(ARN, MODELS, ACCOUNT)[2], self.hash)
        self.assertNotEqual(m.build_template(ARN, MODELS[:1], ACCOUNT)[2], self.hash)
        with patch.dict(m.OPERATIONS, {"summary": "Changed summary instructions"}):
            self.assertNotEqual(m.build_template(ARN, MODELS, ACCOUNT)[2], self.hash)

    def test_profile_preflight_rejects_global_inactive_and_changed_destinations(self):
        good = {
            "inferenceProfileArn": ARN,
            "status": "ACTIVE",
            "models": [{"modelArn": arn} for arn in MODELS],
        }
        self.assertEqual(m.validate_profile(good, ACCOUNT), (ARN, MODELS))
        for change in (
            {"status": "INACTIVE"},
            {"inferenceProfileArn": ARN.replace("/au.", "/global.")},
            {"models": []},
            {
                "models": [
                    {"modelArn": MODELS[0].replace("ap-southeast-2", "us-east-1")}
                ]
            },
            {"inferenceProfileArn": ARN.replace(ACCOUNT, "000000000000")},
        ):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                m.validate_profile({**good, **change}, ACCOUNT)

    def test_create_and_wait_then_resume_without_mutation(self):
        missing = m.AwsError(
            "An error occurred (ValidationError): Stack does not exist"
        )
        aws = FakeAws(
            [
                ("describe-stacks", missing),
                ("create-stack", {}),
                ("describe-stacks", self.stack("CREATE_IN_PROGRESS")),
                ("describe-stacks", self.stack()),
            ]
        )
        self.assertEqual(
            m.ensure_stack(aws, "test", self.template, self.tags, lambda _: None),
            {"RoleArn": ROLE},
        )
        aws = FakeAws(
            [
                ("describe-stacks", self.stack()),
                ("get-template", {"TemplateBody": self.template}),
                ("describe-stacks", self.stack()),
            ]
        )
        m.ensure_stack(aws, "test", self.template, self.tags, lambda _: None)
        self.assertEqual(
            [call[1] for call in aws.calls],
            ["describe-stacks", "get-template", "describe-stacks"],
        )

    def test_access_denial_never_treated_as_absent_stack(self):
        aws = FakeAws(
            [
                (
                    "describe-stacks",
                    m.AwsError("An error occurred (AccessDenied): denied"),
                )
            ]
        )
        with self.assertRaises(m.AwsError):
            m.ensure_stack(aws, "test", self.template, self.tags)
        self.assertEqual(len(aws.calls), 1)

    def test_existing_stack_ownership_and_template_drift_refused(self):
        aws = FakeAws(
            [("describe-stacks", self.stack(tags={"ManagedBy": "someone-else"}))]
        )
        with self.assertRaisesRegex(RuntimeError, "ownership"):
            m.ensure_stack(aws, "test", self.template, self.tags)
        aws = FakeAws(
            [("describe-stacks", self.stack()), ("get-template", {"TemplateBody": {}})]
        )
        with self.assertRaisesRegex(RuntimeError, "differs"):
            m.ensure_stack(aws, "test", self.template, self.tags)

    def test_rollback_and_stack_timeout_are_not_success(self):
        for final in ("ROLLBACK_COMPLETE", "CREATE_FAILED", "DELETE_IN_PROGRESS"):
            aws = FakeAws(
                [
                    ("describe-stacks", self.stack()),
                    ("get-template", {"TemplateBody": self.template}),
                    ("describe-stacks", self.stack(final)),
                ]
            )
            with self.subTest(final=final), self.assertRaisesRegex(RuntimeError, final):
                m.ensure_stack(aws, "test", self.template, self.tags, lambda _: None)
        aws = FakeAws(
            [
                ("describe-stacks", self.stack()),
                ("get-template", {"TemplateBody": self.template}),
            ]
            + [("describe-stacks", self.stack("CREATE_IN_PROGRESS"))] * 120
        )
        with patch("builtins.print"), self.assertRaisesRegex(RuntimeError, "Timed out"):
            m.ensure_stack(aws, "test", self.template, self.tags, lambda _: None)

    def test_prepare_no_model_invocation_and_prepared_rerun(self):
        aws = FakeAws(
            [
                ("get-flow", self.flow("NotPrepared")),
                ("prepare-flow", {}),
                ("get-flow", self.flow("Preparing")),
                ("get-flow", self.flow()),
            ]
        )
        result = m.prepare_flow(aws, FLOW, self.graphs["summary"], ROLE, lambda _: None)
        self.assertEqual(result["live_eval"], "not_run")
        self.assertEqual(result["version"], "DRAFT")
        self.assertEqual(
            [call[1] for call in aws.calls],
            ["get-flow", "prepare-flow", "get-flow", "get-flow"],
        )
        aws = FakeAws([("get-flow", self.flow()), ("get-flow", self.flow())])
        m.prepare_flow(aws, FLOW, self.graphs["summary"], ROLE, lambda _: None)
        self.assertEqual(len(aws.calls), 2)

    def test_flow_drift_refused_and_node_reordering_allowed(self):
        graph = copy.deepcopy(self.graphs["summary"])
        graph["nodes"][1]["configuration"]["prompt"]["sourceConfiguration"]["inline"][
            "modelId"
        ] = "other"
        aws = FakeAws([("get-flow", self.flow(graph=graph))])
        with self.assertRaisesRegex(RuntimeError, "drift"):
            m.prepare_flow(aws, FLOW, self.graphs["summary"], ROLE)
        graph = copy.deepcopy(self.graphs["summary"])
        graph["nodes"].reverse()
        aws = FakeAws(
            [("get-flow", self.flow(graph=graph)), ("get-flow", self.flow(graph=graph))]
        )
        m.prepare_flow(aws, FLOW, self.graphs["summary"], ROLE)

    def test_failed_and_never_prepared_flow_not_success(self):
        aws = FakeAws(
            [
                ("get-flow", self.flow("Preparing")),
                (
                    "get-flow",
                    {**self.flow("Failed"), "validations": [{"message": "Bad graph"}]},
                ),
            ]
        )
        with self.assertRaisesRegex(RuntimeError, "Bad graph"):
            m.prepare_flow(aws, FLOW, self.graphs["summary"], ROLE, lambda _: None)
        aws = FakeAws([("get-flow", self.flow("Preparing"))] * 61)
        with self.assertRaisesRegex(RuntimeError, "Timed out"):
            m.prepare_flow(aws, FLOW, self.graphs["summary"], ROLE, lambda _: None)

    def test_render_only_makes_no_aws_calls(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(m.Path, "cwd", return_value=Path(temporary)),
            patch.object(m.sys, "argv", ["launcher", "--render-only"]),
            patch.object(m, "Aws") as factory,
            patch.object(m.os, "umask"),
            patch("builtins.print"),
        ):
            m.main()
            factory.return_value.assert_not_called()
            manifest = json.loads(
                next(Path(temporary).glob("threadly-flows-*/manifest.json")).read_text()
            )
            self.assertEqual(manifest["status"], "render_only")
            self.assertFalse(manifest["backend_enabled"])

    def test_partial_run_records_completed_flows_without_false_success(self):
        profile = {
            "status": "ACTIVE",
            "inferenceProfileArn": ARN,
            "models": [{"modelArn": arn} for arn in MODELS],
        }
        outputs = {
            "RoleArn": ROLE,
            **{op.title() + "FlowArn": FLOW for op in m.OPERATIONS},
        }
        aws = FakeAws(
            [
                ("get-caller-identity", {"Account": ACCOUNT}),
                ("get-inference-profile", profile),
                ("validate-template", {}),
            ]
        )
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(m.Path, "cwd", return_value=Path(temporary)),
            patch.object(m.sys, "argv", ["launcher"]),
            patch.object(m, "Aws", return_value=aws),
            patch.object(m, "ensure_stack", return_value=outputs),
            patch.object(
                m,
                "prepare_flow",
                side_effect=[
                    {"status": "Prepared", "live_eval": "not_run"},
                    RuntimeError("fixture access failure"),
                ],
            ),
            patch.object(m.os, "umask"),
            patch("builtins.print"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture access failure"):
                m.main()
            manifest = json.loads(
                next(Path(temporary).glob("threadly-flows-*/manifest.json")).read_text()
            )
            self.assertEqual(manifest["status"], "incomplete")
            self.assertEqual(set(manifest["flows"]), {"summary"})
            self.assertFalse(manifest["backend_enabled"])

    def test_cli_json_is_argv_not_shell_code(self):
        with patch.object(
            m.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "{}", ""),
        ) as run:
            m.Aws()(
                "cloudformation",
                "validate-template",
                {"TemplateBody": "$(touch unsafe) `id`"},
            )
        args, kwargs = run.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 120)


if __name__ == "__main__":
    unittest.main()
