"""Reproducible Haiku classification infrastructure; no AWS calls when rendering."""

import re

from app.assistant.summary import digest
from app.classification.contracts import Decision
from app.classification.flows import FLOW_RELEASE, HAIKU, REGION, definition, models, prompt_variant


def bundle(account, prompts):
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("Invalid account")
    candidates = models(account)
    if set(prompts) != set(candidates):
        raise ValueError("Provide only the selected Haiku prompt version")
    graphs = {key: definition(prompts[key]) for key in candidates}
    fingerprint = digest({"release": FLOW_RELEASE, "graphs": graphs,
                          "prompts": {key: prompt_variant(model)
                                      for key, model in candidates.items()},
                          "schema": Decision.model_json_schema()})
    tags = {"ManagedBy": "threadly-classification", "ReleaseHash": fingerprint}
    resources, outputs = {}, {}
    for key, model in candidates.items():
        role = "HaikuRole"
        statements = [{"Effect": "Allow",
                       "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                       "Resource": [model]}]
        statements.append({
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": [f"arn:aws:bedrock:{r}::foundation-model/{HAIKU}"
                         for r in (REGION, "ap-southeast-4")],
            "Condition": {"StringEquals": {"bedrock:InferenceProfileArn": model}},
        })
        statements.append({"Effect": "Allow", "Action": ["bedrock:RenderPrompt"],
                           "Resource": [prompts[key], prompts[key].rsplit(":", 1)[0]]})
        resources[role] = {
            "Type": "AWS::IAM::Role",
            "Metadata": {"com.aws.cloudformation.Context": {
                "why": "Classification role limits inference to the selected Haiku 4.5 model.",
                "must": ["No mail, storage, secrets or tool permissions.",
                         "Bedrock trust restricted to this account and Sydney Flows."],
            }},
            "Properties": {
                "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                    "Effect": "Allow", "Principal": {"Service": "bedrock.amazonaws.com"},
                    "Action": "sts:AssumeRole", "Condition": {
                        "StringEquals": {"aws:SourceAccount": account},
                        "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{REGION}:{account}:flow/*"},
                    },
                }]},
                "Policies": [{"PolicyName": "SelectedClassificationModel",
                              "PolicyDocument": {"Version": "2012-10-17",
                                                 "Statement": statements}}],
                "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
            },
        }
        outputs[role + "Arn"] = {"Description": role + " ARN",
                                 "Value": {"Fn::GetAtt": [role, "Arn"]}}
    template = {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Roles for API-provisioned classification Flows; no app activation.",
        "Metadata": {"AWSToolsMetrics": {"AWSAgentToolkit": "aws-cloudformation@3"},
                     "ReleaseHash": fingerprint},
        "Resources": resources, "Outputs": outputs,
    }
    return template, graphs, fingerprint, tags


def caller_policy(target):
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["bedrock:InvokeFlow"],
         "Resource": [target.flow_arn + "/alias/" + target.alias_id]},
        {"Effect": "Allow", "Action": ["bedrock:GetFlowAlias", "bedrock:GetFlowVersion"],
         "Resource": [target.flow_arn, target.flow_arn + "/alias/" + target.alias_id]},
        {"Effect": "Allow", "Action": ["bedrock:GetPrompt"],
         "Resource": [target.prompt_arn, target.prompt_arn.rsplit(":", 1)[0]]},
    ]}
