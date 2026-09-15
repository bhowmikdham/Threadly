"""Pin dispatch at task acceptance; a changed config affects new tasks only."""

import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.api.errors import ApiError
from app.assistant import drafting, routing
from app.assistant.summary import GeneratedSummary, digest
from app.config import get_settings
from app.model_client.structured import reject_duplicate_keys
from app.schemas.assistant import StrictModel

RELEASE = "contextual-flows-1.0.0"
OPERATIONS = ("summarise_thread", "draft_reply", "draft_new")
PROFILE = "au.anthropic.claude-haiku-4-5-20251001-v1:0"


class NativeEntry(StrictModel):
    implementation: Literal["native"] = "native"


class FlowEntry(StrictModel):
    implementation: Literal["bedrock_flow"]
    region: Literal["ap-southeast-2"]
    flow_arn: str = Field(
        pattern=r"^arn:aws:bedrock:ap-southeast-2:[0-9]{12}:flow/[a-zA-Z0-9]{10}$"
    )
    alias_id: str = Field(pattern=r"^[a-zA-Z0-9]{10}$")
    version: str = Field(pattern=r"^[1-9][0-9]{0,4}$")
    model_profile_arn: str
    execution_role_arn: str = Field(pattern=r"^arn:aws:iam::[0-9]{12}:role/(service-role/)?.+$")
    definition_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeout_seconds: int = Field(default=75, ge=5, le=90)
    max_input_bytes: int = Field(default=96000, ge=1000, le=96000)
    max_output_bytes: int = Field(default=30000, ge=1000, le=30000)
    max_events: int = Field(default=64, ge=2, le=128)

    @model_validator(mode="after")
    def pinned_target(self):
        if self.alias_id == "TSTALIASID":
            raise ValueError("Test aliases cannot execute durable application tasks")
        account = self.flow_arn.split(":")[4]
        expected = f"arn:aws:bedrock:{self.region}:{account}:inference-profile/{PROFILE}"
        if self.model_profile_arn != expected or self.execution_role_arn.split(":")[4] != account:
            raise ValueError(
                "Flow, execution role and selected Haiku profile must share an account"
            )
        if self.definition_hash != digest(flow_definition(self.model_profile_arn)):
            raise ValueError("Unknown Flow definition contract")
        return self


Entry = Annotated[NativeEntry | FlowEntry, Field(discriminator="implementation")]


class Manifest(StrictModel):
    schema_version: Literal["1.0"]
    operations: dict[str, Entry]

    @model_validator(mode="after")
    def complete_known_operations(self):
        if set(self.operations) != set(OPERATIONS):
            raise ValueError("Configure exactly the three installed generation operations")
        return self


def flow_definition(profile_arn: str) -> dict:
    # The backend supplies its pinned prompt and masked, owner-bound excerpts.
    # No read callbacks or externally executable nodes are allowed in this release.
    return {
        "nodes": [
            {
                "name": "Input",
                "type": "Input",
                "configuration": {"input": {}},
                "outputs": [{"name": "document", "type": "String"}],
            },
            {
                "name": "Generate",
                "type": "Prompt",
                "configuration": {
                    "prompt": {
                        "sourceConfiguration": {
                            "inline": {
                                "modelId": profile_arn,
                                "templateType": "TEXT",
                                "templateConfiguration": {
                                    "text": {
                                        "text": "{{request}}",
                                        "inputVariables": [{"name": "request"}],
                                    }
                                },
                                "inferenceConfiguration": {
                                    "text": {"maxTokens": 2500, "temperature": 0.0}
                                },
                            }
                        }
                    }
                },
                "inputs": [{"name": "request", "type": "String", "expression": "$.data"}],
                "outputs": [{"name": "modelCompletion", "type": "String"}],
            },
            {
                "name": "Output",
                "type": "Output",
                "configuration": {"output": {}},
                "inputs": [{"name": "document", "type": "String", "expression": "$.data"}],
            },
        ],
        "connections": [
            {
                "name": "InputToGenerate",
                "source": "Input",
                "target": "Generate",
                "type": "Data",
                "configuration": {"data": {"sourceOutput": "document", "targetInput": "request"}},
            },
            {
                "name": "GenerateToOutput",
                "source": "Generate",
                "target": "Output",
                "type": "Data",
                "configuration": {
                    "data": {"sourceOutput": "modelCompletion", "targetInput": "document"}
                },
            },
        ],
    }


def contract_hash() -> str:
    return digest(
        {
            "version": RELEASE,
            "native_release": routing.release_manifest(),
            "summary_schema": GeneratedSummary.model_json_schema(),
            "draft_schema": drafting.GeneratedDraft.model_json_schema(),
            "context_policy": "owner-bound-snapshot-no-callbacks-v1",
            "validation": "native-artifact-validation-v1",
        }
    )


def load_manifest() -> Manifest | None:
    raw = get_settings().assistant_workflow_manifest
    if not raw.strip():
        return None
    try:
        if len(raw.encode()) > 16000:
            raise ValueError("Manifest too large")
        result = Manifest.model_validate(json.loads(raw, object_pairs_hook=reject_duplicate_keys))
        if any(isinstance(entry, FlowEntry) for entry in result.operations.values()):
            if get_settings().inference_provider != "bedrock":
                raise ValueError("Flow configuration requires Bedrock mode")
        return result
    except (ValueError, TypeError):
        raise ApiError(
            503, "workflow_configuration_invalid", "Workflow configuration is unavailable."
        ) from None


def release_manifest() -> dict:
    manifest = load_manifest()
    if manifest is None:
        return routing.release_manifest()
    return {
        "workflow": RELEASE,
        "native_release": routing.release_manifest(),
        "contract_hash": contract_hash(),
        "registry": manifest.model_dump(),
    }


def pinned_manifest(release: dict) -> Manifest:
    try:
        if set(release) != {"workflow", "native_release", "contract_hash", "registry"}:
            raise ValueError("Unknown release keys")
        if (
            release["workflow"] != RELEASE
            or release["contract_hash"] != contract_hash()
            or release["native_release"] != routing.release_manifest()
        ):
            raise ValueError("Pinned backend implementation unavailable")
        return Manifest.model_validate(release["registry"])
    except (ValueError, TypeError, KeyError):
        raise ApiError(
            503, "release_unavailable", "The saved workflow release is unavailable."
        ) from None
