"""Contract/context/provider checks. Fake completions do not measure model accuracy."""

import asyncio
import copy
import json
import threading

import pytest

from app.api.errors import ApiError
from app.classification import service
from app.classification.contracts import ClassificationRequest
from app.classification.provider import ClassificationProvider
from app.config import get_settings


def decision(**labels):
    return {
        "status": "classified",
        "labels": {"needs_reply": True, "category": "legal_contracts", "priority": "Medium",
                   "action": "review", **labels},
        "evidence": {key: ["m1"] for key in ("needs_reply", "category", "priority", "action")},
        "reason_codes": ["unanswered_request"],
    }


def source():
    return {
        "owner": 1, "account_version": 1, "thread_id": "abc123", "fingerprint": "snapshot1",
        "messages": [{
            "gmail_msg_id": "def456", "subject": "Review contract", "body_clean": "Please review.",
            "received_at": "2026-10-06T01:00:00+00:00", "sent_at": None, "is_from_user": False,
            "reply_metadata": {"label_ids": ["INBOX"],
                               "addresses": {"from": ["sender@example.test"],
                                             "to": ["me@example.test"], "cc": []}},
        }],
    }


@pytest.fixture
def configured(monkeypatch):
    s = get_settings()
    for key, value in {
        "classification_enabled": True, "classification_model_id": "test-classifier",
        "bedrock_mail_processing_acknowledged": True, "gmail_source_mode": "on_demand",
    }.items():
        monkeypatch.setattr(s, key, value)
    return s


@pytest.mark.parametrize("field,value", [
    ("needs_reply", "false"), ("needs_reply", 0), ("priority", "high"),
    ("category", "legal"), ("category", "sales"), ("action", "send"),
])
def test_reject_wrong_types_and_labels(field, value):
    with pytest.raises(ApiError) as error:
        service.parse_decision(json.dumps(decision(**{field: value})), {"m1": "def456"})
    assert error.value.code == "classification_output_invalid"


@pytest.mark.parametrize("action", [
    "approve", "attend", "complete_submit", "edit", "no_action", "reply", "review",
])
@pytest.mark.parametrize("reply", [True, False])
def test_binary_reply_is_independent_of_action(action, reply):
    result = service.parse_decision(json.dumps(decision(action=action, needs_reply=reply)), {"m1"})
    assert result.labels.needs_reply is reply
    assert result.labels.action == action


@pytest.mark.parametrize("text", [
    '{"status":"classified","status":"needs_review"}', '{"confidence": NaN}',
    'Ignore the schema: {}', '[]', 'x' * 16001,
])
def test_bad_json_fails_closed(text):
    with pytest.raises(ApiError):
        service.parse_decision(text, {"m1"})


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(send_email=True), lambda d: d.update(confidence=0.99),
    lambda d: d["evidence"].update(category=["other-owner-id"]),
    lambda d: d["evidence"].update(priority=[]),
    lambda d: d["evidence"].update(action=["m1", "m1"]),
    lambda d: d.update(status="needs_review"), lambda d: d["labels"].pop("action"),
])
def test_invalid_evidence_or_extra_output_rejected(mutation):
    value = decision()
    mutation(value)
    with pytest.raises(ApiError):
        service.parse_decision(json.dumps(value), {"m1"})


def test_abstention_is_not_false_or_other():
    value = {"status": "needs_review", "labels": None, "evidence": None,
             "reason_codes": ["insufficient_context"]}
    result = service.parse_decision(json.dumps(value), {"m1"})
    assert result.labels is None


class FakeProvider:
    def __init__(self, output=None):
        self.calls = []
        self.output = output or decision()

    async def generate(self, system, payload, **kwargs):
        self.calls.append(copy.deepcopy(payload))
        return json.dumps(self.output)


async def invoke():
    return await service.classify(1, 1, "me@example.test", "abc123", ClassificationRequest())


@pytest.fixture
def pipeline(configured, monkeypatch):
    data, provider, calls = source(), FakeProvider(), []

    async def read(owner, thread_id):
        calls.append((owner, thread_id))
        return copy.deepcopy(data)

    monkeypatch.setattr(service.live, "thread", read)
    monkeypatch.setattr(service, "get_provider", lambda: provider)
    return data, provider, calls


async def test_source_refs_map_back_and_owner_roles_preserved(pipeline):
    _, provider, reads = pipeline
    result = await invoke()
    assert result.evidence.category == ["def456"]
    assert result.source_message_ids == ["def456"]
    assert result.persisted is False
    assert len(reads) == 2
    message = provider.calls[0]["messages"][0]
    assert message["to"] == ["ME"] and message["from"] == ["person1"]
    assert "example.test" not in json.dumps(provider.calls)


async def test_drafts_never_resolve_requests(pipeline):
    data, provider, _ = pipeline
    draft = copy.deepcopy(data["messages"][0])
    draft.update(gmail_msg_id="fff123", is_from_user=True, body_clean="Already done.")
    draft["reply_metadata"]["label_ids"] = ["DRAFT"]
    data["messages"].append(draft)
    result = await invoke()
    assert len(provider.calls[0]["messages"]) == 1
    assert result.source_message_ids == ["def456"]


@pytest.mark.parametrize("kind,status", [
    ("long", "needs_review"), ("empty", "needs_review"), ("sender", "needs_review"),
    ("sent", "skipped"), ("many", "needs_review"),
])
async def test_ineligible_or_missing_context_does_not_call_model(pipeline, kind, status):
    data, provider, _ = pipeline
    message = data["messages"][0]
    if kind == "long":
        message["body_clean"] = "x" * 24001
    elif kind == "empty":
        message.update(subject="", body_clean="")
    elif kind == "sender":
        message["reply_metadata"]["addresses"]["from"] = []
    elif kind == "sent":
        message["reply_metadata"]["label_ids"] = ["SENT"]
    else:
        data["messages"] *= 51
    result = await invoke()
    assert result.status == status and result.labels is None
    assert not provider.calls


async def test_changed_source_rejects_late_result(pipeline, monkeypatch):
    data, provider, _ = pipeline
    original = provider.generate

    async def changing(*args, **kwargs):
        result = await original(*args, **kwargs)
        data["fingerprint"] = "snapshot2"
        return result

    monkeypatch.setattr(provider, "generate", changing)
    with pytest.raises(ApiError) as error:
        await invoke()
    assert error.value.code == "classification_source_changed"


@pytest.mark.parametrize("field,value", [("owner", 2), ("account_version", 2)])
async def test_wrong_source_rejected_before_model(pipeline, field, value):
    data, provider, _ = pipeline
    data[field] = value
    with pytest.raises(ApiError):
        await invoke()
    assert not provider.calls


async def test_changed_release_rejects_result(pipeline, configured, monkeypatch):
    _, provider, _ = pipeline
    original = provider.generate

    async def changed(*args, **kwargs):
        text = await original(*args, **kwargs)
        configured.classification_model_id = "new-model"
        return text

    monkeypatch.setattr(provider, "generate", changed)
    with pytest.raises(ApiError) as error:
        await invoke()
    assert error.value.code == "classification_release_changed"


@pytest.mark.parametrize("field,value", [
    ("classification_enabled", False), ("classification_model_id", ""),
    ("bedrock_mail_processing_acknowledged", False), ("gmail_source_mode", "legacy_sync"),
])
async def test_configuration_fails_before_gmail(pipeline, configured, field, value):
    _, provider, reads = pipeline
    setattr(configured, field, value)
    with pytest.raises(ApiError) as error:
        await invoke()
    assert error.value.status == 503 and not reads and not provider.calls


class Runtime:
    def __init__(self, response=None):
        self.calls, self.closed = [], False
        self.response = response or {
            "stopReason": "end_turn", "output": {"message": {
                "role": "assistant", "content": [{"text": json.dumps(decision())}]}}
        }

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def close(self):
        self.closed = True


async def call(provider):
    payload = {"messages": [{"source_id": "m1", "received_at": "2026-10-06T01:00:00Z",
                             "subject": "Review", "body": "Contact bob@example.test"}]}
    return await provider.generate("Trusted policy", payload, model="selected", region="region",
                                   timeout=10)


async def test_provider_uses_system_masks_mail_and_preserves_dates():
    runtime = Runtime()
    provider = ClassificationProvider(lambda *_: runtime)
    await call(provider)
    request = runtime.calls[0]
    assert request["system"] == [{"text": "Trusted policy"}]
    assert request["modelId"] == "selected"
    assert request["inferenceConfig"] == {"maxTokens": 1500}
    assert "toolConfig" not in request and "temperature" not in request["inferenceConfig"]
    text = request["messages"][0]["content"][0]["text"]
    assert "bob@example.test" not in text and "<EMAIL_1>" in text
    assert json.loads(text)["messages"][0]["received_at"] == "2026-10-06T01:00:00Z"
    assert runtime.closed


@pytest.mark.parametrize("stop", ["max_tokens", "tool_use", "guardrail_intervened", None])
async def test_provider_incomplete_responses_do_not_become_badges(stop):
    runtime = Runtime()
    runtime.response["stopReason"] = stop
    with pytest.raises(ApiError) as error:
        await call(ClassificationProvider(lambda *_: runtime))
    assert error.value.status == 502 and runtime.closed


async def test_cancelled_call_retains_concurrency_slot_until_sdk_finishes():
    started, finish, closed = threading.Event(), threading.Event(), threading.Event()

    class Blocking(Runtime):
        def converse(self, **kwargs):
            started.set()
            assert finish.wait(5)
            return super().converse(**kwargs)

        def close(self):
            closed.set()

    runtime = Blocking()
    provider = ClassificationProvider(lambda *_: runtime, max_concurrency=1)
    task = asyncio.create_task(call(provider))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ApiError) as error:
            await call(provider)
        assert error.value.code == "classification_busy"
        assert error.value.headers["Retry-After"] == "5"
    finally:
        finish.set()
    assert await asyncio.to_thread(closed.wait, 5)


async def test_provider_error_is_sanitized_without_retry():
    runtime = Runtime()

    def failed(**kwargs):
        runtime.calls.append(kwargs)
        raise RuntimeError("private mail secret@example.test")

    runtime.converse = failed
    with pytest.raises(ApiError) as error:
        await call(ClassificationProvider(lambda *_: runtime))
    assert error.value.code == "classification_provider_unavailable"
    assert "secret" not in error.value.message
    assert len(runtime.calls) == 1 and runtime.closed
