"""Module 8: fallback chain + PII masking on cloud egress."""
import json

import httpx
import pytest

from app.model_client.client import ModelClient
from app.model_client.providers import OllamaProvider, OpenRouterProvider


def ollama_up_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        if request.url.path == "/api/generate":
            lines = [
                json.dumps({"response": "Hello "}),
                json.dumps({"response": "world"}),
                json.dumps({"done": True}),
            ]
            return httpx.Response(200, text="\n".join(lines))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def ollama_down_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mac is off")

    return httpx.MockTransport(handler)


def openrouter_transport(seen: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        sse = (
            'data: {"choices":[{"delta":{"content":"cloud "}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"reply"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_uses_ollama_when_mac_healthy():
    mc = ModelClient(ollama=OllamaProvider(ollama_up_transport()), openrouter=OpenRouterProvider())
    text, info = await mc.generate("summarise this")
    assert text == "Hello world" and info.provider == "ollama"


@pytest.mark.asyncio
async def test_falls_back_to_openrouter_and_masks_pii():
    seen: dict = {}
    mc = ModelClient(
        ollama=OllamaProvider(ollama_down_transport()),
        openrouter=OpenRouterProvider(openrouter_transport(seen)),
    )
    text, info = await mc.generate("email bob@corp.com about the invoice")
    assert text == "cloud reply" and info.provider == "openrouter"
    sent = seen["body"]["messages"][0]["content"]
    assert "bob@corp.com" not in sent and "<EMAIL_1>" in sent  # masked before egress
    assert seen["body"]["reasoning"] == {"exclude": True}  # thinking OFF
