#!/usr/bin/env python3
"""Create one isolated concise-summary console Flow; preserve existing Flows/roles."""

import argparse
import copy
import importlib.util
import os
import re
import tempfile
from pathlib import Path

import cloudshell_flows as provision

POLICY_PATH = Path(__file__).with_name("summary_policy.py")
if not POLICY_PATH.exists():
    POLICY_PATH = (
        Path(__file__).resolve().parents[2] / "backend/app/assistant/summary_policy.py"
    )
spec = importlib.util.spec_from_file_location("threadly_summary_policy", POLICY_PATH)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def build_template(profile_arn, model_arns, account):
    old_template, _, _, _ = provision.build_template(profile_arn, model_arns, account)
    graph = provision.definition("summary", profile_arn)
    inline = graph["nodes"][1]["configuration"]["prompt"]["sourceConfiguration"][
        "inline"
    ]
    inline["templateConfiguration"]["text"]["text"] = policy.CONSOLE_PROMPT
    inline["inferenceConfiguration"]["text"] = {"maxTokens": 1200, "temperature": 0.0}
    fingerprint = provision.digest(
        {
            "release": policy.VERSION,
            "graph": graph,
            "account": account,
            "models": model_arns,
        }
    )
    tags = {
        "ManagedBy": "threadly-summary-quality",
        "ReleaseHash": fingerprint,
        "Stage": "prototype",
    }
    role = copy.deepcopy(old_template["Resources"]["FlowRole"])
    role["Properties"]["Tags"] = [{"Key": k, "Value": v} for k, v in tags.items()]
    flow = {
        "Type": "AWS::Bedrock::Flow",
        "Properties": {
            "Name": "threadly-summary-quality-" + fingerprint[:12],
            "Description": "Concise summary console experiment. " + policy.VERSION,
            "ExecutionRoleArn": {"Fn::GetAtt": ["FlowRole", "Arn"]},
            "Definition": provision.cfn_keys(graph),
            "Tags": tags,
            "TestAliasTags": tags,
        },
    }
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Threadly concise summary experiment; does not update application or old Flows.",
        "Metadata": {"ReleaseHash": fingerprint, "BackendEnabled": False},
        "Resources": {"FlowRole": role, "SummaryFlow": flow},
        "Outputs": {
            "RoleArn": {"Value": {"Fn::GetAtt": ["FlowRole", "Arn"]}},
            "SummaryFlowArn": {"Value": {"Fn::GetAtt": ["SummaryFlow", "Arn"]}},
        },
    }
    return template, graph, fingerprint, tags


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    aws = provision.Aws()
    if args.render_only:
        account = "123456789012"
        profile = {
            "status": "ACTIVE",
            "inferenceProfileArn": f"arn:aws:bedrock:{provision.REGION}:{account}:inference-profile/{provision.PROFILE}",
            "models": [
                {"modelArn": f"arn:aws:bedrock:{r}::foundation-model/{provision.MODEL}"}
                for r in ("ap-southeast-2", "ap-southeast-4")
            ],
        }
    else:
        account = aws("sts", "get-caller-identity")["Account"]
        if not re.fullmatch(r"[0-9]{12}", account):
            raise RuntimeError("Invalid account identity")
        profile = aws(
            "bedrock",
            "get-inference-profile",
            {"inferenceProfileIdentifier": provision.PROFILE},
        )
    profile_arn, models = provision.validate_profile(profile, account)
    template, graph, fingerprint, tags = build_template(profile_arn, models, account)
    directory = Path(tempfile.mkdtemp(prefix="threadly-summary-", dir=Path.cwd()))
    stack_name = "threadly-summary-quality-" + fingerprint[:12]
    provision.write_json(directory / "template.json", template)
    provision.write_json(directory / "summary.flow.json", graph)
    (directory / "prompt.txt").write_text(policy.CONSOLE_PROMPT)
    print(f"Stack: {stack_name}\nAssets: {directory}", flush=True)
    if args.render_only:
        print("Rendered synthetic assets only. No AWS calls.")
        return
    print(
        "Creating ONE new summary test Flow and a restricted execution role. "
        "Existing Flows and application configuration stay unchanged. "
        "No model invocation; testing the Flow later incurs model usage.",
        flush=True,
    )
    aws(
        "cloudformation",
        "validate-template",
        {"TemplateBody": provision.canonical(template)},
    )
    outputs = provision.ensure_stack(aws, stack_name, template, tags)
    result = provision.prepare_flow(
        aws, outputs["SummaryFlowArn"], graph, outputs["RoleArn"]
    )
    provision.write_json(
        directory / "manifest.json",
        {
            "release": policy.VERSION,
            "summary_contract": "console-only",
            "flow": result,
            "backend_enabled": False,
            "live_evaluation": "not_run",
        },
    )
    print(
        "Prepared summary quality test Flow. Open:\n"
        f"https://{provision.REGION}.console.aws.amazon.com/bedrock/home?region={provision.REGION}"
        f"#/flows/{result['id']}/builder"
    )
    print(
        "Paste a messages-array JSON example into the NEW Flow. "
        "Review its summary; Prepared is not evidence of output quality."
    )


if __name__ == "__main__":
    main()
