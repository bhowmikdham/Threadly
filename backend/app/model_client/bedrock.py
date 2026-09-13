"""Bounded text-only Bedrock Converse adapter (ADR 003).

The synchronous SDK runs off the event loop. This first adapter buffers one
response, validates its stop reason, then exposes one chunk to existing SSE
callers. Tools, reasoning blocks and incomplete output never become final text.
"""
import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any

from app.config import get_settings
from app.model_client.providers import ProviderError


def _runtime_client():
    # Keep imports and credential discovery out of application startup.
    import boto3
    from botocore.config import Config

    settings = get_settings()
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.bedrock_region,
        config=Config(
            connect_timeout=5,
            read_timeout=settings.bedrock_read_timeout_s,
            retries={"mode": "standard", "total_max_attempts": 1},
        ),
    )


class BedrockProvider:
    name = "bedrock"

    def __init__(self, client_factory: Callable[[], Any] | None = None):
        self._client_factory = client_factory or _runtime_client

    def _generate(self, prompt: str, model: str, max_tokens: int) -> str:
        client = None
        try:
            client = self._client_factory()
            result = client.converse(
                modelId=model,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
            )
            if result.get("stopReason") != "end_turn":
                raise ProviderError("bedrock did not produce a complete text response")
            message = result["output"]["message"]
            blocks = message["content"]
            if message["role"] != "assistant" or not blocks:
                raise ProviderError("bedrock returned an invalid response")
            if any(set(block) != {"text"} or not isinstance(block["text"], str)
                   for block in blocks):
                raise ProviderError("bedrock returned unsupported content")
            text = "".join(block["text"] for block in blocks)
            if not text.strip():
                raise ProviderError("bedrock returned empty text")
            return text
        except ProviderError:
            raise
        except Exception:
            # SDK errors may contain request data. Expose no raw message/body.
            raise ProviderError("bedrock request failed") from None
        finally:
            if client is not None:
                with suppress(Exception):
                    client.close()

    async def stream(
        self, prompt: str, *, model: str, max_tokens: int
    ) -> AsyncIterator[str]:
        if not model.strip():
            raise ProviderError("bedrock model ID is not configured")
        if not 1 <= max_tokens <= 4096:
            raise ProviderError("bedrock output token limit must be between 1 and 4096")
        # Cancellation stops the awaiting caller; the bounded SDK request finishes
        # and closes its client in the worker. No writes or retries occur here.
        yield await asyncio.to_thread(self._generate, prompt, model, max_tokens)
