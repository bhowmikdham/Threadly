"""Separate Converse tool adapter; strict text-only workflows remain unchanged."""

import asyncio
from contextlib import suppress

from app.config import get_settings
from app.model_client.bedrock import _runtime_client
from app.model_client.providers import ProviderError
from app.pii.masking import mask_structure, unmask


class ConversationProviderError(ProviderError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


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
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            allowed = {
                "ThrottlingException",
                "ModelTimeoutException",
                "ServiceUnavailableException",
                "InternalServerException",
                "AccessDeniedException",
                "ValidationException",
            }
            raise ConversationProviderError(
                code if code in allowed else "provider_unavailable"
            ) from None
        finally:
            if client is not None:
                with suppress(Exception):
                    client.close()

    async def decide(self, system, messages, tools):
        return await asyncio.to_thread(self._call, system, messages, tools)
