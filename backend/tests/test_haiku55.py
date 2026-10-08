"""Synthetic migration regressions; no inference, mail reads or external writes."""

import json

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.classification import flows, service
from app.classification.flow_release import bundle
from app.classification.provider import ClassificationProvider
from app.config import get_settings
from app.model_client.bedrock import BedrockProvider
from app.model_client.conversation import ConversationModel
from app.model_client.haiku import HAIKU_45, HAIKU_55, request_options
from app.model_client.providers import ProviderError
from tests.test_bedrock import FakeRuntime
from tests.test_classification import Runtime
from tests.test_classification_flows import Control, target
from tests.test_conversation_model import Client, response

ACCOUNT = "123456789012"
PROFILE = f"arn:aws:bedrock:ap-southeast-2:{ACCOUNT}:inference-profile/au.{HAIKU_55}"
REASONING = {"reasoningContent": {"reasoningText": {"text": "", "signature": "opaque"}}}
MESSAGES = [{"role": "user", "content": [{"text": "Draft a short hello."}]}]


@pytest.fixture
def settings(monkeypatch):
    value = get_settings()
    monkeypatch.setattr(value, "inference_provider", "bedrock")
    monkeypatch.setattr(value, "bedrock_model_id", PROFILE)
    monkeypatch.setattr(value, "bedrock_mail_processing_acknowledged", True)
    return value


@pytest.mark.parametrize("model", [HAIKU_55, "au." + HAIKU_55, PROFILE])
def test_55_request_shape_and_explicit_45_rollback(model):
    assert request_options(model, 1800, temperature=0) == {
        "inferenceConfig": {"maxTokens": 2340},
        "additionalModelRequestFields": {"thinking": {"type": "disabled"}},
    }
    assert request_options("au." + HAIKU_45, 1800, temperature=0) == {
        "inferenceConfig": {"maxTokens": 1800, "temperature": 0},
    }
    assert request_options("au." + HAIKU_45, 1500) == {
        "inferenceConfig": {"maxTokens": 1500},
    }


async def test_text_generation_selects_text_and_bounds_new_tokenizer():
    raw = FakeRuntime(blocks=[REASONING, {"text": "A draft."}])
    result = [v async for v in BedrockProvider(lambda: raw).stream(
        "Write to alice@example.test", model=PROFILE, max_tokens=700)]
    assert result == ["A draft."]
    assert raw.calls[0]["modelId"] == PROFILE
    assert raw.calls[0]["inferenceConfig"] == {"maxTokens": 910}
    validate_parameters(raw.calls[0], Session().get_service_model("bedrock-runtime")
                        .operation_model("Converse").input_shape)
    assert raw.closed


@pytest.mark.parametrize("stop,blocks", [
    ("refusal", [{"text": "No"}]),
    ("max_tokens", [REASONING]),
    ("end_turn", [REASONING]),
    ("end_turn", [{"toolUse": {}}]),
])
async def test_text_refusal_truncation_and_nontext_fail_closed(stop, blocks):
    raw = FakeRuntime(stop=stop, blocks=blocks)
    with pytest.raises(ProviderError):
        _ = [v async for v in BedrockProvider(lambda: raw).stream(
            "Synthetic", model=PROFILE, max_tokens=100)]
    assert len(raw.calls) == 1 and raw.closed


async def test_forced_tools_mask_arguments_and_keep_history_valid(settings):
    raw = Client(response())
    history = [{"role": "user", "content": [{"text": "Find alex@example.test"}]}]
    answer = await ConversationModel(lambda: raw).decide("policy", history, {"tools": []})
    call = raw.calls[0]
    assert call["inferenceConfig"] == {"maxTokens": 2340}
    assert call["additionalModelRequestFields"] == {"thinking": {"type": "disabled"}}
    assert call["toolConfig"]["toolChoice"] == {"any": {}}
    assert "alex@example.test" not in json.dumps(call)
    assert answer["content"][0]["toolUse"]["input"]["text"] == "Contact alex@example.test"
    history += [answer, {"role": "user", "content": [{"toolResult": {
        "toolUseId": "call-1", "content": [{"json": {"found": True}}], "status": "success",
    }}]}]
    await ConversationModel(lambda: raw).decide("policy", history, {"tools": []})
    assert raw.calls[-1]["messages"][-1]["role"] == "user"
    assert raw.calls[-1]["messages"][-2]["content"][0]["toolUse"]["toolUseId"] == "call-1"


@pytest.mark.parametrize("history", [[], [{"role": "assistant", "content": [{"text": "{"}]}]])
async def test_assistant_prefill_rejected_before_inference(settings, history):
    raw = Client(response())
    with pytest.raises(ProviderError, match="final user turn"):
        await ConversationModel(lambda: raw).decide("policy", history, {"tools": []})
    assert raw.calls == []


@pytest.mark.parametrize("kind", ["reasoning", "refusal", "max_tokens"])
async def test_no_incomplete_tool_or_signed_reasoning_replay(settings, kind):
    value = response()
    if kind == "reasoning":
        value["output"]["message"]["content"].insert(0, REASONING)
    else:
        value["stopReason"] = kind
    raw = Client(value)
    with pytest.raises(ProviderError):
        await ConversationModel(lambda: raw).decide("policy", MESSAGES, {"tools": []})
    assert len(raw.calls) == 1 and raw.closed


@pytest.mark.parametrize("stop", ["end_turn", "refusal", "max_tokens"])
async def test_direct_classification_never_parses_reasoning_as_badges(stop):
    raw = Runtime()
    raw.response["stopReason"] = stop
    raw.response["output"]["message"]["content"].insert(0, REASONING)
    payload = {"messages": [{"source_id": "m1", "received_at": "2026-10-08T00:00:00Z",
                             "subject": "Review", "body": "Contact alice@example.test"}]}
    provider = ClassificationProvider(lambda *_: raw)
    if stop == "end_turn":
        text = await provider.generate(service.PROMPT, payload, model=PROFILE,
                                       region="ap-southeast-2", timeout=10)
        assert service.parse_decision(text, {"m1"}).status == "classified"
    else:
        with pytest.raises(ApiError, match="complete classification"):
            await provider.generate(service.PROMPT, payload, model=PROFILE,
                                    region="ap-southeast-2", timeout=10)
    assert raw.calls[0]["inferenceConfig"] == {"maxTokens": 1950}
    assert "alice@example.test" not in json.dumps(raw.calls)
    assert "2026-10-08T00:00:00Z" in json.dumps(raw.calls)
    assert raw.closed


def test_classification_flow_keeps_old_pin_and_renders_distinct_55_candidate():
    old = target()
    new = flows.ClassificationFlow.model_validate({**old.model_dump(),
                                                  "model_profile_arn": PROFILE})
    old_prompt = flows.prompt_variant(old.model_profile_arn)
    new_prompt = flows.prompt_variant(new.model_profile_arn)
    assert old_prompt["inferenceConfiguration"] == {"text": {"maxTokens": 1500}}
    assert "additionalModelRequestFields" not in old_prompt
    assert new_prompt["inferenceConfiguration"] == {"text": {"maxTokens": 1950}}
    assert new_prompt["additionalModelRequestFields"] == {"thinking": {"type": "disabled"}}
    assert digest(old_prompt) != digest(new_prompt)
    validate_parameters({"name": "synthetic", "variants": [new_prompt]},
                        Session().get_service_model("bedrock-agent")
                        .operation_model("CreatePrompt").input_shape)
    flows.verify_target(Control(old), old)
    flows.verify_target(Control(new), new)
    template, _, _, _ = bundle(ACCOUNT, {"haiku": new.prompt_arn}, model=HAIKU_55)
    statements = template["Resources"]["HaikuRole"]["Properties"]["Policies"][0][
        "PolicyDocument"]["Statement"]
    assert statements[0]["Resource"] == [PROFILE]
    assert set(statements[1]["Resource"]) == {
        f"arn:aws:bedrock:{region}::foundation-model/{HAIKU_55}"
        for region in ("ap-southeast-2", "ap-southeast-4")
    }
    assert statements[1]["Condition"]["StringEquals"]["bedrock:InferenceProfileArn"] == PROFILE


@pytest.mark.parametrize("profile", [PROFILE.replace("au.", "global."),
                                     PROFILE.replace("ap-southeast-2", "us-east-1"),
                                     PROFILE.replace(ACCOUNT, "999999999999")])
def test_classification_rejects_non_au_or_other_account_profiles(profile):
    with pytest.raises(ValueError):
        flows.ClassificationFlow.model_validate({**target().model_dump(),
                                                "model_profile_arn": profile})
