import json

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


async def test_decoded_masking_preserves_unicode_escapes_and_nested_json():
    """The old serialized mask corrupted the \\u2028 escape into invalid JSON."""
    email = "alex@example.test"
    phone = "+61 412 345 678"
    card = "4111111111111111"
    context = json.dumps(
        {
            "note": "A \u2028 1234 appointment",
            "contact": email,
            "phone": phone,
            "card": card,
            "toolUseId": email,
            "contacts": {email: "confirmed"},
        }
    )
    result = response()
    result["output"]["message"]["content"][0]["toolUse"]["input"] = {
        "text": "Contact <EMAIL_1> at <PHONE_1>; card <CARD_1>",
        "metadata": {"contact": "<EMAIL_1>"},
    }
    client = Client(result)
    answer = await ConversationModel(lambda: client).decide(
        f"Contact {email}",
        [
            {"role": "user", "content": [{"text": context}]},
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "lookup-2028-1234",
                            "content": [{"json": {"contact": email, "card": card}}],
                            "status": "success",
                        }
                    }
                ],
            },
        ],
        {"tools": []},
    )

    call = client.calls[0]
    sent = json.dumps(call)
    for private in (email, phone, card):
        assert private not in sent
    assert call["system"][0]["text"] == "Contact <EMAIL_1>"
    inner = json.loads(call["messages"][0]["content"][0]["text"])
    assert inner["note"] == "A \u2028 1234 appointment"
    assert inner["contact"] == "<EMAIL_1>"
    assert inner["phone"] == "<PHONE_1>"
    assert inner["card"] == "<CARD_1>"
    assert inner["toolUseId"] == "<EMAIL_1>"
    assert inner["contacts"] == {"<EMAIL_1>": "confirmed"}
    observation = call["messages"][1]["content"][0]["toolResult"]
    assert observation["toolUseId"] == "lookup-2028-1234"
    assert observation["content"][0]["json"]["contact"] == "<EMAIL_1>"
    assert observation["content"][0]["json"]["card"] == "<CARD_1>"
    restored = answer["content"][0]["toolUse"]
    assert restored["input"] == {
        "text": f"Contact {email} at {phone}; card {card}",
        "metadata": {"contact": email},
    }
    assert restored["toolUseId"] == "call-1"
    assert client.closed


async def test_malformed_embedded_json_is_masked_without_parse_failure():
    client = Client(response())
    invalid_json = '{"message": "A \\u2028 1234, email alex@example.test"'
    await ConversationModel(lambda: client).decide(
        "policy", [{"role": "user", "content": [{"text": invalid_json}]}], {"tools": []}
    )
    assert "alex@example.test" not in str(client.calls)
    assert client.calls[0]["messages"][0]["content"][0]["text"].startswith('{"message":')
    assert client.closed


async def test_literal_placeholder_does_not_unmask_into_private_data():
    client = Client(response())
    result = await ConversationModel(lambda: client).decide(
        "policy",
        [{"role": "user", "content": [{"text": "<EMAIL_1> and alex@example.test"}]}],
        {"tools": []},
    )
    assert client.calls[0]["messages"][0]["content"][0]["text"] == ("<EMAIL_1> and <EMAIL_2>")
    assert result["content"][0]["toolUse"]["input"]["text"] == "Contact <EMAIL_1>"


async def test_escaped_literal_placeholder_in_json_is_also_reserved():
    client = Client(response())
    embedded = '{"text":"\\u003cEMAIL_1\\u003e and alex@example.test"}'
    await ConversationModel(lambda: client).decide(
        "policy", [{"role": "user", "content": [{"text": embedded}]}], {"tools": []}
    )
    decoded = json.loads(client.calls[0]["messages"][0]["content"][0]["text"])
    assert decoded["text"] == "<EMAIL_1> and <EMAIL_2>"


async def test_private_dictionary_keys_and_escaped_placeholder_key():
    value = response()
    value["output"]["message"]["content"][0]["toolUse"]["input"] = {"<EMAIL_2>": "found"}
    client = Client(value)
    embedded = '{"\\u003cEMAIL_1\\u003e":"literal","alice@example.test":"private"}'
    result = await ConversationModel(lambda: client).decide(
        "policy",
        [
            {"role": "user", "content": [{"text": embedded}]},
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "call-1",
                            "content": [
                                {
                                    "json": {
                                        "alice@example.test": "value",
                                        "phone_number": 61412345678,
                                        "card_number": 4111111111111111,
                                        "count": 3,
                                    }
                                }
                            ],
                            "status": "success",
                        }
                    }
                ],
            },
        ],
        {"tools": []},
    )
    sent = client.calls[0]["messages"]
    assert "alice@example.test" not in json.dumps(sent)
    assert json.loads(sent[0]["content"][0]["text"]) == {
        "<EMAIL_1>": "literal",
        "<EMAIL_2>": "private",
    }
    observation = sent[1]["content"][0]["toolResult"]
    assert observation["toolUseId"] == "call-1"
    assert observation["content"][0]["json"] == {
        "<EMAIL_2>": "value",
        "phone_number": "<PHONE_1>",
        "card_number": "<CARD_1>",
        "count": 3,
    }
    assert result["content"][0]["toolUse"]["input"] == {"alice@example.test": "found"}


async def test_provider_tool_identity_is_never_unmasked():
    value = response()
    value["output"]["message"]["content"][0]["toolUse"]["toolUseId"] = "<EMAIL_1>"
    client = Client(value)
    result = await ConversationModel(lambda: client).decide(
        "policy", [{"role": "user", "content": [{"text": "alex@example.test"}]}], {"tools": []}
    )
    assert result["content"][0]["toolUse"]["toolUseId"] == "<EMAIL_1>"
    assert result["content"][0]["toolUse"]["input"]["text"] == "Contact alex@example.test"


async def test_masking_failure_is_sanitized_before_provider_call(monkeypatch):
    from app.model_client import conversation

    def fail(_value):
        raise ValueError("PRIVATE-INVALID-JSON")

    monkeypatch.setattr(conversation, "mask_structure", fail)
    client = Client(response())
    with pytest.raises(ProviderError) as exc:
        await ConversationModel(lambda: client).decide("policy", [], {"tools": []})
    assert "PRIVATE" not in str(exc.value)
    assert client.calls == []


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
