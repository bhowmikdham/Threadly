"""Offline checks for the separate summary experiment; no model invocations."""

import copy
import sys
import unittest
from pathlib import Path

from botocore.session import Session
from botocore.validate import validate_parameters

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloudshell_summary as q

ACCOUNT = "123456789012"
PROFILE = (
    f"arn:aws:bedrock:ap-southeast-2:{ACCOUNT}:inference-profile/{q.provision.PROFILE}"
)
MODELS = [
    f"arn:aws:bedrock:{r}::foundation-model/{q.provision.MODEL}"
    for r in ("ap-southeast-2", "ap-southeast-4")
]


class SummaryExperimentTests(unittest.TestCase):
    def test_one_summary_and_restricted_role_only(self):
        template, graph, _, _ = q.build_template(PROFILE, MODELS, ACCOUNT)
        self.assertEqual(set(template["Resources"]), {"FlowRole", "SummaryFlow"})
        self.assertFalse(template["Metadata"]["BackendEnabled"])
        policy = template["Resources"]["FlowRole"]["Properties"]["Policies"][0][
            "PolicyDocument"
        ]
        for statement in policy["Statement"]:
            self.assertEqual(
                set(statement["Action"]),
                {"bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"},
            )
        inline = graph["nodes"][1]["configuration"]["prompt"]["sourceConfiguration"][
            "inline"
        ]
        self.assertEqual(inline["modelId"], PROFILE)
        self.assertEqual(
            inline["templateConfiguration"]["text"]["text"], q.policy.CONSOLE_PROMPT
        )
        self.assertEqual(inline["inferenceConfiguration"]["text"]["temperature"], 0.0)

    def test_graph_matches_aws_create_shape(self):
        _, graph, _, _ = q.build_template(PROFILE, MODELS, ACCOUNT)
        validate_parameters(
            {
                "name": "summary-quality-test",
                "definition": graph,
                "executionRoleArn": f"arn:aws:iam::{ACCOUNT}:role/test",
            },
            Session()
            .get_service_model("bedrock-agent")
            .operation_model("CreateFlow")
            .input_shape,
        )

    def test_old_prototypes_are_unchanged_and_names_are_distinct(self):
        old = copy.deepcopy(q.provision.build_template(PROFILE, MODELS, ACCOUNT))
        template, _, fingerprint, _ = q.build_template(PROFILE, MODELS, ACCOUNT)
        self.assertEqual(old, q.provision.build_template(PROFILE, MODELS, ACCOUNT))
        self.assertNotEqual(old[2], fingerprint)
        self.assertNotEqual(
            old[0]["Resources"]["SummaryFlow"]["Properties"]["Name"],
            template["Resources"]["SummaryFlow"]["Properties"]["Name"],
        )

    def test_change_prompt_changes_release_identity(self):
        original = q.policy.CONSOLE_PROMPT
        try:
            before = q.build_template(PROFILE, MODELS, ACCOUNT)[2]
            q.policy.CONSOLE_PROMPT += "\nA changed prompt"
            after = q.build_template(PROFILE, MODELS, ACCOUNT)[2]
            self.assertNotEqual(before, after)
        finally:
            q.policy.CONSOLE_PROMPT = original


if __name__ == "__main__":
    unittest.main()
