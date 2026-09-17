"""Render a reviewed Flow release bundle; this command never calls AWS.

python -m app.workflows.release --profile-arn ARN --output DIRECTORY
Deploy stack.json using CloudFormation, then prepare and publish numbered versions.
assemble consumes the resulting published IDs and validates a candidate registry.
"""

import argparse
import json
from pathlib import Path

from app.assistant.summary import digest
from app.workflows.registry import OPERATIONS, PROFILE, FlowEntry, Manifest, flow_definition


def cfn_keys(value):
    if isinstance(value, dict):
        return {key[0].upper() + key[1:]: cfn_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [cfn_keys(item) for item in value]
    return value


def template(profile_arn: str, *, operations=OPERATIONS) -> dict:
    parts = profile_arn.split(":", 5)
    if (
        len(parts) != 6
        or parts[:4] != ["arn", "aws", "bedrock", "ap-southeast-2"]
        or len(parts[4]) != 12
        or not parts[4].isdigit()
        or parts[5] != "inference-profile/" + PROFILE
    ):
        raise ValueError("Use the account's Australian Haiku 4.5 inference profile ARN")
    account = parts[4]
    graph = flow_definition(profile_arn)
    suffix = digest(graph)[:12]
    model_id = PROFILE.removeprefix("au.")
    model_arns = [
        f"arn:aws:bedrock:{region}::foundation-model/{model_id}"
        for region in ("ap-southeast-2", "ap-southeast-4")
    ]
    resources = {
        "RuntimeFlowRole": {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "AssumeRolePolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "bedrock.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                            "Condition": {
                                "StringEquals": {"aws:SourceAccount": account},
                                "ArnLike": {
                                    "AWS:SourceArn": (
                                        f"arn:aws:bedrock:ap-southeast-2:{account}:flow/*"
                                    )
                                },
                            },
                        }
                    ],
                },
                "Policies": [
                    {
                        "PolicyName": "SelectedHaiku",
                        "PolicyDocument": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": ["bedrock:InvokeModel"],
                                    "Resource": [profile_arn],
                                },
                                {
                                    "Effect": "Allow",
                                    "Action": ["bedrock:InvokeModel"],
                                    "Resource": model_arns,
                                    "Condition": {
                                        "StringEquals": {"bedrock:InferenceProfileArn": profile_arn}
                                    },
                                },
                            ],
                        },
                    }
                ],
            },
        }
    }
    outputs = {"RoleArn": {"Value": {"Fn::GetAtt": ["RuntimeFlowRole", "Arn"]}}}
    for op in operations:
        logical = "".join(word.title() for word in op.split("_")) + "Flow"
        resources[logical] = {
            "Type": "AWS::Bedrock::Flow",
            "Properties": {
                "Name": f"threadly-runtime-{op.replace('_', '-')}-{suffix}",
                "Description": "Threadly prompt contract v1. Backend owns context and validation.",
                "ExecutionRoleArn": {"Fn::GetAtt": ["RuntimeFlowRole", "Arn"]},
                "Definition": cfn_keys(graph),
                "Tags": {"ManagedBy": "threadly-runtime-flows", "DefinitionHash": digest(graph)},
            },
        }
        outputs[logical + "Arn"] = {"Value": {"Fn::GetAtt": [logical, "Arn"]}}
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Threadly generation Flows. Backend activation is a separate release step.",
        "Resources": resources,
        "Outputs": outputs,
    }


def assemble(targets: dict) -> Manifest:
    """Published-target files require real IDs; tests use visibly synthetic fixtures."""
    return Manifest(
        schema_version="1.0",
        operations={op: FlowEntry.model_validate(value) for op, value in targets.items()},
    )


def caller_policy(manifest: Manifest) -> dict:
    entries = [entry for entry in manifest.operations.values() if isinstance(entry, FlowEntry)]
    if not entries:
        raise ValueError("No Flow targets configured")
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeFlow"],
                "Resource": sorted(
                    {entry.flow_arn + "/alias/" + entry.alias_id for entry in entries}
                ),
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock:GetFlowAlias", "bedrock:GetFlowVersion"],
                "Resource": sorted(
                    {
                        resource
                        for entry in entries
                        for resource in (
                            entry.flow_arn,
                            entry.flow_arn + "/alias/" + entry.alias_id,
                        )
                    }
                ),
            },
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--profile-arn")
    source.add_argument("--targets", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--auxiliary", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    from app.workflows import auxiliary

    if args.profile_arn:
        result = template(
            args.profile_arn, operations=auxiliary.OPERATIONS if args.auxiliary else OPERATIONS
        )
        (args.output / "stack.json").write_text(json.dumps(result, indent=2) + "\n")
        (args.output / "flow.json").write_text(
            json.dumps(flow_definition(args.profile_arn), indent=2) + "\n"
        )
        print("Rendered only. Deploy and evaluate before configuring the backend.")
    else:
        targets = json.loads(args.targets.read_text())
        result = (
            auxiliary.Manifest(schema_version="1.0", operations=targets)
            if args.auxiliary
            else assemble(targets)
        )
        (args.output / "candidate-registry.json").write_text(
            result.model_dump_json(indent=2) + "\n"
        )
        (args.output / "worker-policy.json").write_text(
            json.dumps(caller_policy(result), indent=2) + "\n"
        )
        print("Candidate registry only; no IAM attachment, model invocation or backend activation.")


if __name__ == "__main__":
    main()
