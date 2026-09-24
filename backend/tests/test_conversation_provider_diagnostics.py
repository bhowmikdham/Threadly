"""Provider retry and diagnostic boundaries without live model requests."""

import logging

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from app.api.errors import ApiError
from app.config import get_settings
from app.conversation import engine
from app.model_client.conversation import ConversationModel, ConversationProviderError


@pytest.fixture(autouse=True)
def bedrock(monkeypatch):
    monkeypatch.setenv("INFERENCE_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-profile")
    monkeypatch.setenv("BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FailingClient:
    def __init__(self, error):
        self.error = error
        self.closed = False

    def converse(self, **_kwargs):
        raise self.error

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ReadTimeoutError(endpoint_url="https://private.example.test"), "ReadTimeoutError"),
        (
            EndpointConnectionError(endpoint_url="https://private.example.test"),
            "EndpointConnectionError",
        ),
    ],
)
async def test_recognized_transport_failures_have_safe_codes(error, code):
    client = FailingClient(error)
    with pytest.raises(ConversationProviderError) as raised:
        await ConversationModel(lambda: client).decide("policy", [], {"tools": []})
    assert raised.value.code == code
    assert "private" not in str(raised.value).lower()
    assert raised.value.elapsed_ms is not None
    assert client.closed


async def test_aws_failure_carries_only_allowlisted_metadata():
    client = FailingClient(
        ClientError(
            {
                "Error": {"Code": "ModelNotReadyException", "Message": "PRIVATE RESPONSE"},
                "ResponseMetadata": {"HTTPStatusCode": 429, "RequestId": "safe-request-123"},
            },
            "Converse",
        )
    )
    with pytest.raises(ConversationProviderError) as raised:
        await ConversationModel(lambda: client).decide("policy", [], {"tools": []})
    assert raised.value.code == "ModelNotReadyException"
    assert raised.value.http_status == 429
    assert raised.value.request_id == "safe-request-123"
    assert "PRIVATE" not in str(raised.value)


async def test_unexpected_sdk_data_cannot_enter_diagnostics():
    client = FailingClient(
        ClientError(
            {
                "Error": {"Code": "private@example.test", "Message": "PRIVATE RESPONSE"},
                "ResponseMetadata": {
                    "HTTPStatusCode": "PRIVATE",
                    "RequestId": "private@example.test",
                },
            },
            "Converse",
        )
    )
    with pytest.raises(ConversationProviderError) as raised:
        await ConversationModel(lambda: client).decide("policy", [], {"tools": []})
    assert raised.value.code == "provider_unavailable"
    assert raised.value.http_status is None
    assert raised.value.request_id is None
    assert "PRIVATE" not in str(raised.value)


class DecisionModel:
    def __init__(self, *failures):
        self.failures = list(failures)
        self.calls = 0

    async def decide(self, _system, _messages, _tools):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "call-1",
                        "name": "respond",
                        "input": {"kind": "message", "text": "Ready"},
                    }
                }
            ],
        }


class Runtime:
    evidence = {}


@pytest.mark.parametrize("code", ["ModelNotReadyException", "ReadTimeoutError"])
async def test_transient_provider_failure_retries_with_bounded_delay(monkeypatch, caplog, code):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(engine.asyncio, "sleep", sleep)
    monkeypatch.setattr(engine.random, "uniform", lambda _a, _b: 0.25)
    model = DecisionModel(
        ConversationProviderError(
            code, http_status=503, request_id="safe-request-123", elapsed_ms=17
        )
    )
    with caplog.at_level(logging.WARNING, logger="threadly.conversation.provider"):
        response = await engine.run({"user_turn": "hey"}, Runtime(), model)
    assert response["text"] == "Ready"
    assert model.calls == 2
    assert delays == [2.25]
    assert "code=" + code in caplog.text
    assert "http_status=503" in caplog.text
    assert "request_id=safe-request-123" in caplog.text
    assert "elapsed_ms=17 attempt=1" in caplog.text


@pytest.mark.parametrize("code", ["AccessDeniedException", "ValidationException"])
async def test_terminal_provider_failure_does_not_retry_or_leak(monkeypatch, caplog, code):
    async def no_sleep(_delay):
        pytest.fail("Terminal errors must not sleep for retry")

    monkeypatch.setattr(engine.asyncio, "sleep", no_sleep)
    model = DecisionModel(
        ConversationProviderError(code, request_id="private@example.test", elapsed_ms=17)
    )
    with caplog.at_level(logging.WARNING, logger="threadly.conversation.provider"):
        with pytest.raises(ApiError) as raised:
            await engine.run({"user_turn": "hey"}, Runtime(), model)
    assert model.calls == 1
    assert raised.value.code == "conversation_provider_unavailable"
    assert code not in raised.value.message
    assert "private@example.test" not in caplog.text


async def test_transient_failure_stops_after_three_attempts(monkeypatch):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(engine.asyncio, "sleep", sleep)
    monkeypatch.setattr(engine.random, "uniform", lambda _a, _b: 0)
    model = DecisionModel(
        *(ConversationProviderError("ServiceUnavailableException") for _ in range(3))
    )
    with pytest.raises(ApiError) as raised:
        await engine.run({"user_turn": "hey"}, Runtime(), model)
    assert raised.value.code == "conversation_provider_unavailable"
    assert model.calls == 3
    assert delays == [2, 4]


async def test_provider_retry_does_not_start_without_time_for_response(monkeypatch):
    original_timeout = engine.asyncio.timeout
    monkeypatch.setattr(engine.asyncio, "timeout", lambda _seconds: original_timeout(5))

    async def no_sleep(_delay):
        pytest.fail("There is no time left for another bounded attempt")

    monkeypatch.setattr(engine.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(engine.random, "uniform", lambda _a, _b: 0)
    model = DecisionModel(ConversationProviderError("ModelNotReadyException"))
    with pytest.raises(ApiError) as raised:
        await engine.run({"user_turn": "hey"}, Runtime(), model)
    assert raised.value.code == "conversation_provider_unavailable"
    assert model.calls == 1
