"""Separate Converse tool adapter; strict text-only workflows remain unchanged."""

import asyncio
import re
import time
from contextlib import suppress

from app.config import get_settings
from app.model_client.bedrock import _runtime_client
from app.model_client.providers import ProviderError
from app.pii.masking import mask_structure, unmask

AWS_ERROR_CODES = frozenset(
    {
        "ThrottlingException",
        "ModelTimeoutException",
        "ModelNotReadyException",
        "ServiceUnavailableException",
        "InternalServerException",
        "AccessDeniedException",
        "ValidationException",
    }
)
TRANSPORT_ERROR_CODES = frozenset(
    {"ReadTimeoutError", "ConnectTimeoutError", "EndpointConnectionError", "ConnectionClosedError"}
)
_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{1,80}\Z")


class ConversationProviderError(ProviderError):
    """Only allowlisted transport metadata may cross into internal diagnostics."""

    def __init__(self, code, *, http_status=None, request_id=None, elapsed_ms=None):
        self.code = (
            code
            if isinstance(code, str) and code in AWS_ERROR_CODES | TRANSPORT_ERROR_CODES
            else "provider_unavailable"
        )
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )
        self.request_id = (
            request_id
            if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id)
            else None
        )
        self.elapsed_ms = (
            elapsed_ms if type(elapsed_ms) is int and 0 <= elapsed_ms <= 300_000 else None
        )
        super().__init__(self.code)


class ConversationModel:
    def __init__(self, client_factory=None):
        self.factory = client_factory or _runtime_client

    def _call(self, system, messages, tools):
        settings = get_settings()
        if settings.inference_provider != "bedrock" or not settings.bedrock_model_id:
            raise ProviderError("Conversation requires configured Bedrock")
        if not settings.bedrock_mail_processing_acknowledged:
            raise ProviderError("Bedrock mail processing is not acknowledged")
        client = None
        started = time.monotonic()
        try:
            # Mask decoded values instead of serialized JSON, which may contain
            # Unicode escapes that a phone-like match would corrupt. The map is
            # shared across system, history, and nested tool observations.
            masked, mapping = mask_structure({"system": system, "messages": messages})
            client = self.factory()
            result = client.converse(
                modelId=settings.bedrock_model_id,
                system=[{"text": masked["system"]}],
                messages=masked["messages"],
                toolConfig={**tools, "toolChoice": {"any": {}}},
                inferenceConfig={"maxTokens": 1800, "temperature": 0.0},
            )
            if result.get("stopReason") != "tool_use":
                raise ProviderError("Conversation did not return a complete decision")
            message = result["output"]["message"]
            if message.get("role") != "assistant" or not message.get("content"):
                raise ProviderError("Invalid conversation decision")

            def restore(value):
                if isinstance(value, str):
                    return unmask(value, mapping)
                if isinstance(value, list):
                    return [restore(v) for v in value]
                if isinstance(value, dict):
                    return {
                        unmask(k, mapping) if isinstance(k, str) else k: restore(v)
                        for k, v in value.items()
                    }
                return value

            calls = [b["toolUse"] for b in message["content"] if "toolUse" in b]
            if not 1 <= len(calls) <= 4:
                raise ProviderError("Invalid tool count")
            for c in calls:
                if (
                    not {"toolUseId", "name", "input"}.issubset(c)
                    or set(c) - {"toolUseId", "name", "input", "type"}
                    or c.get("type", "tool_use") != "tool_use"
                    or not isinstance(c["input"], dict)
                ):
                    raise ProviderError("Invalid tool request")
                if not isinstance(c["toolUseId"], str) or len(c["toolUseId"]) > 200:
                    raise ProviderError("Invalid tool identity")
            # Tool IDs/names are provider transport values. Only semantic tool
            # arguments can contain placeholders to restore.
            return {
                "role": "assistant",
                "content": [
                    {"toolUse": {**call, "input": restore(call["input"])}} for call in calls
                ],
            }
        except ProviderError:
            raise
        except Exception as exc:
            response = getattr(exc, "response", None)
            response = response if isinstance(response, dict) else {}
            error = response.get("Error")
            metadata = response.get("ResponseMetadata")
            error = error if isinstance(error, dict) else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            code = error.get("Code")
            # Botocore transport exceptions have no AWS error response. Restrict
            # classification to named SDK classes; never log their messages or URL.
            cls = type(exc)
            if not isinstance(code, str) or code not in AWS_ERROR_CODES:
                code = (
                    cls.__name__
                    if cls.__module__ == "botocore.exceptions"
                    and cls.__name__ in TRANSPORT_ERROR_CODES
                    else "provider_unavailable"
                )
            raise ConversationProviderError(
                code,
                http_status=metadata.get("HTTPStatusCode"),
                request_id=metadata.get("RequestId"),
                elapsed_ms=round((time.monotonic() - started) * 1000),
            ) from None
        finally:
            if client is not None:
                with suppress(Exception):
                    client.close()

    async def decide(self, system, messages, tools):
        return await asyncio.to_thread(self._call, system, messages, tools)
