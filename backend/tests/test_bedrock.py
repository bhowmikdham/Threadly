"""Synthetic SDK responses; these are adapter checks, not live model evaluations."""
import asyncio
import threading

import pytest

from app.config import Settings, get_settings
from app.model_client.bedrock import BedrockProvider
from app.model_client.client import GenResult, ModelClient
from app.model_client.providers import ProviderError


class FakeRuntime:
    def __init__(self, *, stop="end_turn", blocks=None, error=None):
        self.stop = stop
        self.blocks = blocks if blocks is not None else [{"text": "A summary."}]
        self.error = error
        self.calls = []
        self.closed = False
        self.thread_id = None

    def converse(self, **kwargs):
        self.thread_id = threading.get_ident()
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {
            "stopReason": self.stop,
            "output": {"message": {"role": "assistant", "content": self.blocks}},
        }

    def close(self):
        self.closed = True


@pytest.fixture
def bedrock_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "inference_provider", "bedrock")
    monkeypatch.setattr(settings, "bedrock_model_id", "test-main")
    monkeypatch.setattr(settings, "bedrock_small_model_id", "test-small")
    return settings


async def test_bedrock_selection_masks_input_and_uses_worker_thread(bedrock_settings):
    runtime = FakeRuntime()
    client = ModelClient(bedrock=BedrockProvider(lambda: runtime))
    text, info = await client.generate("Summarise mail from bob@example.test", max_tokens=500)
    assert text == "A summary."
    assert (info.provider, info.model) == ("bedrock", "test-main")
    call = runtime.calls[0]
    assert call["modelId"] == "test-main"
    assert call["inferenceConfig"] == {"maxTokens": 500, "temperature": 0.0}
    assert "bob@example.test" not in str(call)
    assert "<EMAIL_1>" in str(call)
    assert runtime.thread_id != threading.get_ident()
    assert runtime.closed


async def test_small_model_and_fallback_to_configured_main(bedrock_settings):
    runtime = FakeRuntime()
    client = ModelClient(bedrock=BedrockProvider(lambda: runtime))
    _, info = await client.generate("Route", small=True)
    assert info.model == "test-small"
    bedrock_settings.bedrock_small_model_id = ""
    _, info = await client.generate("Route", small=True)
    assert info.model == "test-main"


@pytest.mark.parametrize("stop", [
    "max_tokens", "tool_use", "guardrail_intervened", "content_filtered", None,
])
async def test_incomplete_or_blocked_text_is_rejected(stop):
    runtime = FakeRuntime(stop=stop)
    with pytest.raises(ProviderError, match="complete text"):
        _ = [s async for s in BedrockProvider(lambda: runtime).stream(
            "input", model="test", max_tokens=100
        )]
    assert runtime.closed


@pytest.mark.parametrize("blocks", [[], [{"text": " "}], [{"toolUse": {}}],
                                        [{"text": "x", "reasoningContent": {}}]])
async def test_unexpected_content_rejected(blocks):
    runtime = FakeRuntime(blocks=blocks)
    with pytest.raises(ProviderError):
        _ = [s async for s in BedrockProvider(lambda: runtime).stream(
            "input", model="test", max_tokens=100
        )]
    assert runtime.closed


async def test_failure_is_sanitized_and_never_falls_back(bedrock_settings):
    class ForbiddenFallback:
        async def health(self, *args):
            pytest.fail("Bedrock must not probe the legacy provider")

    runtime = FakeRuntime(error=RuntimeError("private-email@example.test secret"))
    client = ModelClient(
        ollama=ForbiddenFallback(), bedrock=BedrockProvider(lambda: runtime)
    )
    with pytest.raises(ProviderError, match="^bedrock request failed$") as exc:
        await client.generate("input")
    assert "secret" not in str(exc.value)
    assert runtime.closed


@pytest.mark.parametrize(("model", "cap"), [("", 100), ("test", 0), ("test", 4097)])
async def test_invalid_configuration_does_not_call_sdk(model, cap):
    runtime = FakeRuntime()
    with pytest.raises(ProviderError):
        _ = [s async for s in BedrockProvider(lambda: runtime).stream(
            "input", model=model, max_tokens=cap
        )]
    assert not runtime.calls


async def test_cancellation_eventually_closes_bounded_sdk_request():
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    class BlockingRuntime(FakeRuntime):
        def converse(self, **kwargs):
            started.set()
            assert release.wait(5)
            return super().converse(**kwargs)

        def close(self):
            closed.set()

    runtime = BlockingRuntime()

    async def consume():
        return [s async for s in BedrockProvider(lambda: runtime).stream(
            "input", model="test", max_tokens=100
        )]

    task = asyncio.create_task(consume())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert await asyncio.to_thread(closed.wait, 5)


def test_invalid_provider_configuration_fails_closed():
    with pytest.raises(ValueError):
        Settings(_env_file=None, inference_provider="typo")


def test_long_model_identity_fits_existing_summary_column():
    first = GenResult("bedrock", "arn:" + "a" * 200)
    second = GenResult("bedrock", "arn:" + "a" * 199 + "b")
    assert len(first.storage_label("1.0")) <= 80
    assert first.storage_label("1.0") != second.storage_label("1.0")
    assert first.storage_label("1.0") != first.storage_label("2.0")
    assert GenResult("fake", "test").storage_label("1") == "fake:test@prompts-1"
