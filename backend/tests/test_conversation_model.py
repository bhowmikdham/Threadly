import pytest

from app.config import get_settings
from app.model_client.conversation import ConversationModel
from app.model_client.providers import ProviderError


@pytest.fixture(autouse=True)
def bedrock(monkeypatch):
    monkeypatch.setenv("INFERENCE_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-profile")
    monkeypatch.setenv("BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Client:
    def __init__(self, result):
        self.result, self.calls, self.closed = result, [], False

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        self.closed = True


def response(**changes):
    return {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "call-1",
                            "name": "respond",
                            "type": "tool_use",
                            "input": {"kind": "message", "text": "Contact <EMAIL_1>"},
                        }
                    }
                ],
            }
        },
        **changes,
    }


async def test_optional_tool_type_and_private_data_roundtrip():
    client = Client(response())
    result = await ConversationModel(lambda: client).decide(
        "policy", [{"role": "user", "content": [{"text": "Find alex@example.test"}]}], {"tools": []}
    )
    assert result["content"][0]["toolUse"]["input"]["text"] == "Contact alex@example.test"
    assert "alex@example.test" not in str(client.calls)
    assert client.calls[0]["toolConfig"]["toolChoice"] == {"any": {}}
    assert client.calls[0]["inferenceConfig"]["maxTokens"] == 1800
    assert client.closed


@pytest.mark.parametrize(
    "result",
    [
        response(stopReason="max_tokens"),
        response(stopReason="end_turn"),
        RuntimeError("PRIVATE-PROVIDER-BODY"),
    ],
)
async def test_rejects_partial_and_sanitizes_sdk_errors(result):
    client = Client(result)
    with pytest.raises(ProviderError) as exc:
        await ConversationModel(lambda: client).decide("policy", [], {"tools": []})
    assert "PRIVATE" not in str(exc.value)
    assert client.closed


async def test_server_side_tools_cannot_run():
    value = response()
    value["output"]["message"]["content"][0]["toolUse"]["type"] = "server_tool_use"
    with pytest.raises(ProviderError):
        await ConversationModel(lambda: Client(value)).decide("policy", [], {"tools": []})


async def test_mail_processing_requires_operator_acknowledgement(monkeypatch):
    monkeypatch.setenv("BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED", "false")
    get_settings.cache_clear()
    with pytest.raises(ProviderError, match="not acknowledged"):
        await ConversationModel(lambda: Client(response())).decide("policy", [], {"tools": []})
