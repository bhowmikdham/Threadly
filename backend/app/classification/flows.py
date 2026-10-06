"""Visual classification graph pinned to the selected Claude Haiku 4.5 model."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.classification.contracts import StrictModel
from app.config import get_settings
from app.model_client.structured import reject_duplicate_keys
from app.workflows.bedrock_flows import FlowError, _definition_equal

REGION = "ap-southeast-2"
HAIKU = "anthropic.claude-haiku-4-5-20251001-v1:0"
FLOW_RELEASE = "classification-flow-1.0.0"


def models(account):
    return {
        "haiku": f"arn:aws:bedrock:{REGION}:{account}:inference-profile/au.{HAIKU}",
    }


def prompt_variant(model):
    prompt = Path(__file__).with_name("prompt.txt").read_text()
    return {
        "name": "classify", "modelId": model, "templateType": "CHAT",
        "templateConfiguration": {"chat": {
            "system": [{"text": prompt}],
            "messages": [{"role": "user", "content": [{"text": "{{request}}"}]}],
            "inputVariables": [{"name": "request"}],
        }},
        "inferenceConfiguration": {"text": {"maxTokens": 1500}},
    }


def definition(prompt_arn):
    # Numbered managed CHAT prompts preserve the system/mail boundary.
    return {
        "nodes": [
            {"name": "Input", "type": "Input", "configuration": {"input": {}},
             "outputs": [{"name": "document", "type": "String"}]},
            {"name": "Classify", "type": "Prompt", "configuration": {"prompt": {
                "sourceConfiguration": {"resource": {"promptArn": prompt_arn}}}},
             "inputs": [{"name": "request", "type": "String", "expression": "$.data"}],
             "outputs": [{"name": "modelCompletion", "type": "String"}]},
            {"name": "Output", "type": "Output", "configuration": {"output": {}},
             "inputs": [{"name": "document", "type": "String", "expression": "$.data"}]},
        ],
        "connections": [
            {"name": "InputToClassify", "source": "Input", "target": "Classify", "type": "Data",
             "configuration": {"data": {"sourceOutput": "document", "targetInput": "request"}}},
            {"name": "ClassifyToOutput", "source": "Classify", "target": "Output", "type": "Data",
             "configuration": {"data": {
                 "sourceOutput": "modelCompletion", "targetInput": "document"}}},
        ],
    }


class ClassificationFlow(StrictModel):
    implementation: Literal["bedrock_flow"] = "bedrock_flow"
    region: Literal["ap-southeast-2"] = REGION
    flow_arn: str = Field(
        pattern=r"^arn:aws:bedrock:ap-southeast-2:[0-9]{12}:flow/[A-Za-z0-9]{10}$"
    )
    alias_id: str = Field(pattern=r"^[A-Za-z0-9]{10}$")
    version: str = Field(pattern=r"^[1-9][0-9]{0,4}$")
    model_profile_arn: str
    prompt_arn: str = Field(
        pattern=r"^arn:aws:bedrock:ap-southeast-2:[0-9]{12}:prompt/[A-Za-z0-9]{10}:[1-9][0-9]{0,4}$"
    )
    execution_role_arn: str = Field(pattern=r"^arn:aws:iam::[0-9]{12}:role/(service-role/)?.+$")
    definition_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeout_seconds: int = Field(default=90, ge=5, le=300)
    max_input_bytes: Literal[64000] = 64000
    max_output_bytes: Literal[16000] = 16000
    max_events: Literal[64] = 64

    @model_validator(mode="after")
    def pinned(self):
        account = self.flow_arn.split(":")[4]
        if (self.alias_id == "TSTALIASID" or self.execution_role_arn.split(":")[4] != account
                or self.prompt_arn.split(":")[4] != account
                or self.model_profile_arn not in models(account).values()
                or self.definition_hash != digest(definition(self.prompt_arn))):
            raise ValueError("Unrecognized classification Flow release")
        return self


def configured_flow():
    raw = get_settings().classification_flow_manifest
    try:
        if len(raw.encode()) > 8000:
            raise ValueError("Oversized Flow target")
        return ClassificationFlow.model_validate(
            json.loads(raw, object_pairs_hook=reject_duplicate_keys)
        )
    except (ValueError, TypeError):
        raise ApiError(503, "classification_not_configured",
                       "Configure a published classification Flow release.") from None


def verify_target(client, entry):
    alias = client.get_flow_alias(flowIdentifier=entry.flow_arn, aliasIdentifier=entry.alias_id)
    if (alias.get("arn") != entry.flow_arn + "/alias/" + entry.alias_id
            or alias.get("routingConfiguration") != [{"flowVersion": entry.version}]):
        raise FlowError("workflow_release_changed")
    version = client.get_flow_version(flowIdentifier=entry.flow_arn, flowVersion=entry.version)
    if (version.get("arn") != entry.flow_arn or version.get("version") != entry.version
            or version.get("status") != "Prepared"
            or version.get("executionRoleArn") != entry.execution_role_arn
            or not _definition_equal(version.get("definition", {}),
                                     definition(entry.prompt_arn))):
        raise FlowError("workflow_release_changed")
    prompt = client.get_prompt(promptIdentifier=entry.prompt_arn.rsplit(":", 1)[0],
                               promptVersion=entry.prompt_arn.rsplit(":", 1)[1])
    if (prompt.get("arn") != entry.prompt_arn or prompt.get("defaultVariant") != "classify"
            or prompt.get("variants") != [prompt_variant(entry.model_profile_arn)]):
        raise FlowError("workflow_release_changed")
    return {key: alias.get(key) for key in ("arn", "routingConfiguration", "updatedAt")}
