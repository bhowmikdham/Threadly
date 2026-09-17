"""Additional pinned generation-only Flows; backend reads and approvals stay outside AWS graphs.

This registry is separate from the original three-operation manifest so old queued
contracts and deployments retain their exact schema and hashes.
"""

import json
from typing import Literal

from pydantic import model_validator

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.assistant.summary import release_manifest as model_release
from app.config import get_settings
from app.model_client.client import get_model_client
from app.model_client.structured import reject_duplicate_keys
from app.schemas.assistant import StrictModel
from app.workflows.registry import Entry, FlowEntry, NativeEntry

OPERATIONS = ("route_command", "plan_actions", "interpret_meeting_response")


class Manifest(StrictModel):
    schema_version: Literal["1.0"]
    operations: dict[str, Entry]

    @model_validator(mode="after")
    def complete(self):
        if set(self.operations) != set(OPERATIONS):
            raise ValueError("Configure all three auxiliary operations explicitly")
        return self


def load_manifest():
    raw = get_settings().assistant_auxiliary_workflow_manifest
    if not raw.strip():
        return Manifest(schema_version="1.0", operations={op: NativeEntry() for op in OPERATIONS})
    try:
        if len(raw.encode()) > 16000:
            raise ValueError("Manifest too large")
        result = Manifest.model_validate(json.loads(raw, object_pairs_hook=reject_duplicate_keys))
        if (
            any(isinstance(e, FlowEntry) for e in result.operations.values())
            and get_settings().inference_provider != "bedrock"
        ):
            raise ValueError("Flows require Bedrock mode")
        return result
    except (ValueError, TypeError):
        raise ApiError(
            503,
            "auxiliary_workflow_configuration_invalid",
            "Auxiliary workflow configuration is unavailable.",
        ) from None


def release():
    return {
        "registry": load_manifest().model_dump(),
        "native": model_release(),
        "contract_hash": digest(
            {
                "schema": Manifest.model_json_schema(),
                "policy": "backend-prefetch-generation-only-v1",
            }
        ),
    }


def validate(saved):
    if saved.get("contract_hash") != release()["contract_hash"]:
        raise ApiError(503, "release_unavailable", "Saved auxiliary contract is unavailable.")
    manifest = Manifest.model_validate(saved["registry"])
    # A native adapter uses runtime model settings. Flow targets remain pinned to
    # stored published aliases/versions even if a new manifest is configured.
    if (
        any(isinstance(e, NativeEntry) for e in manifest.operations.values())
        and saved["native"] != model_release()
    ):
        raise ApiError(503, "release_unavailable", "Saved native model configuration changed.")
    return manifest


async def generate(operation, prompt, saved, *, model=None, flow_invoker=None, max_tokens=2500):
    entry = validate(saved).operations[operation]
    if isinstance(entry, FlowEntry):
        from app.workflows.bedrock_flows import FlowInvoker

        result = await (flow_invoker or FlowInvoker()).invoke(entry, prompt)
        return result.text, result.provenance
    text, info = await (model or get_model_client()).generate(prompt, max_tokens=max_tokens)
    return text, {"provider": info.provider, "model": info.model}
