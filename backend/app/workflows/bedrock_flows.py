"""Bounded synchronous AWS event streams off the event loop. No provider writes."""

import asyncio
import json
import threading
import time
from contextlib import suppress
from dataclasses import dataclass

from app.assistant.summary import digest
from app.pii.masking import mask
from app.workflows.registry import FlowEntry, flow_definition


class FlowError(Exception):
    def __init__(self, code: str, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


@dataclass(frozen=True)
class FlowResult:
    text: str
    provenance: dict


def sdk_client(service: str, entry: FlowEntry):
    import boto3
    from botocore.config import Config

    return boto3.client(
        service,
        region_name=entry.region,
        config=Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"mode": "standard", "total_max_attempts": 1},
        ),
    )


def _definition_equal(actual: dict, expected: dict) -> bool:
    def normalized(graph):
        # Only order is insignificant; extra nodes/configuration must be rejected.
        if set(graph) != {"nodes", "connections"}:
            return None
        return {
            key: sorted(graph[key], key=lambda item: item["name"])
            for key in ("nodes", "connections")
        }

    return normalized(actual) == normalized(expected)


def verify_target(client, entry: FlowEntry) -> dict:
    alias = client.get_flow_alias(flowIdentifier=entry.flow_arn, aliasIdentifier=entry.alias_id)
    expected_arn = entry.flow_arn + "/alias/" + entry.alias_id
    if alias.get("arn") != expected_arn or alias.get("routingConfiguration") != [
        {"flowVersion": entry.version}
    ]:
        raise FlowError("workflow_release_changed")
    version = client.get_flow_version(flowIdentifier=entry.flow_arn, flowVersion=entry.version)
    if (
        version.get("arn") != entry.flow_arn
        or version.get("version") != entry.version
        or version.get("status") != "Prepared"
        or version.get("executionRoleArn") != entry.execution_role_arn
        or not _definition_equal(
            version.get("definition", {}), flow_definition(entry.model_profile_arn)
        )
    ):
        raise FlowError("workflow_release_changed")
    return {key: alias.get(key) for key in ("arn", "routingConfiguration", "updatedAt")}


class FlowInvoker:
    def __init__(self, client_factory=None):
        self.client_factory = client_factory or sdk_client

    async def invoke(self, entry: FlowEntry, prompt: str) -> FlowResult:
        masked, _mapping = mask(prompt)
        if not masked.strip() or len(masked.encode()) > entry.max_input_bytes:
            raise FlowError("workflow_input_limit")
        stopped = threading.Event()
        try:
            async with asyncio.timeout(entry.timeout_seconds):
                return await asyncio.to_thread(self._invoke, entry, masked, stopped)
        except TimeoutError:
            raise FlowError("workflow_timeout", True) from None
        finally:
            # Cancelling the await cannot interrupt boto3. The bounded read exits,
            # sees this flag and closes; it cannot start another invocation.
            stopped.set()

    def _invoke(self, entry: FlowEntry, prompt: str, stopped) -> FlowResult:
        control = runtime = stream = None
        deadline = time.monotonic() + entry.timeout_seconds

        def check_time():
            if stopped.is_set() or time.monotonic() >= deadline:
                raise FlowError("workflow_timeout", True)

        try:
            check_time()
            control = self.client_factory("bedrock-agent", entry)
            alias_before = verify_target(control, entry)
            check_time()
            runtime = self.client_factory("bedrock-agent-runtime", entry)
            response = runtime.invoke_flow(
                flowIdentifier=entry.flow_arn,
                flowAliasIdentifier=entry.alias_id,
                enableTrace=False,
                inputs=[
                    {
                        "nodeName": "Input",
                        "nodeOutputName": "document",
                        "content": {"document": prompt},
                    }
                ],
            )
            stream = response["responseStream"]
            output = None
            completed = False
            total_bytes = 0
            for count, event in enumerate(stream, 1):
                check_time()
                total_bytes += len(json.dumps(event, default=str).encode())
                if count > entry.max_events or total_bytes > entry.max_output_bytes + 8192:
                    raise FlowError("workflow_output_limit")
                if not isinstance(event, dict) or len(event) != 1 or completed:
                    raise FlowError("invalid_flow_output")
                kind, value = next(iter(event.items()))
                if kind == "flowOutputEvent":
                    if (
                        output is not None
                        or value.get("nodeName") != "Output"
                        or value.get("nodeType") != "FlowOutputNode"
                        or set(value.get("content", {})) != {"document"}
                    ):
                        raise FlowError("invalid_flow_output")
                    output = value["content"]["document"]
                    if (
                        not isinstance(output, str)
                        or not output.strip()
                        or len(output.encode()) > entry.max_output_bytes
                    ):
                        raise FlowError("invalid_flow_output")
                elif kind == "flowCompletionEvent":
                    if value.get("completionReason") == "INPUT_REQUIRED":
                        raise FlowError("workflow_input_required")
                    if value.get("completionReason") != "SUCCESS" or output is None:
                        raise FlowError("invalid_flow_output")
                    completed = True
                elif kind == "flowMultiTurnInputRequestEvent":
                    raise FlowError("workflow_input_required")
                elif kind in {
                    "throttlingException",
                    "internalServerException",
                    "badGatewayException",
                    "serviceQuotaExceededException",
                }:
                    raise FlowError("workflow_upstream_unavailable", True)
                elif kind in {
                    "accessDeniedException",
                    "resourceNotFoundException",
                    "validationException",
                    "dependencyFailedException",
                }:
                    raise FlowError("workflow_upstream_rejected")
                else:
                    # Trace was disabled. Unknown events are not an assumed success.
                    raise FlowError("invalid_flow_output")
            if not completed:
                raise FlowError("incomplete_flow_output", True)
            check_time()
            alias_after = control.get_flow_alias(
                flowIdentifier=entry.flow_arn, aliasIdentifier=entry.alias_id
            )
            if alias_before != {key: alias_after.get(key) for key in alias_before}:
                raise FlowError("workflow_release_changed")
            check_time()
            return FlowResult(
                output,
                {
                    "provider": "bedrock_flow",
                    "model": entry.model_profile_arn,
                    "flow_arn": entry.flow_arn,
                    "alias_id": entry.alias_id,
                    "flow_version": entry.version,
                    "definition_hash": entry.definition_hash,
                    "output_hash": digest(output),
                    "output_node": "Output",
                },
            )
        except FlowError:
            raise
        except Exception as exc:
            response = getattr(exc, "response", {})
            code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
            retryable = code in {
                "ThrottlingException",
                "InternalServerException",
                "ServiceQuotaExceededException",
            }
            raise FlowError(
                "workflow_upstream_unavailable" if retryable else "workflow_upstream_rejected",
                retryable,
            ) from None
        finally:
            for resource in (stream, runtime, control):
                if resource is not None:
                    with suppress(Exception):
                        resource.close()
