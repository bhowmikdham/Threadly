"""Retry only transient inference errors, including failures during Flow iteration."""

import threading

import pytest
from botocore.exceptions import ClientError

from app.api.errors import ApiError
from app.classification.limits import ClassificationLimits
from app.classification.provider import ClassificationProvider
from tests.test_classification import Runtime
from tests.test_classification_flows import Control, Stream, events, generate, target
from tests.test_classification_flows import Runtime as FlowRuntime


class NoWait:
    def is_set(self):
        return False

    def wait(self, delay):
        return False


@pytest.mark.parametrize("code", ["ThrottlingException", "ServiceUnavailableException"])
def test_converse_transient_failure_recovers(code, caplog):
    runtime = Runtime()
    attempts = []
    original = runtime.converse

    def converse(**kwargs):
        attempts.append(kwargs)
        if len(attempts) < 3:
            raise ClientError({"Error": {"Code": code, "Message": "secret@example.test"}},
                              "Converse")
        return original(**kwargs)

    runtime.converse = converse
    limits = ClassificationLimits(2, 8)
    provider = ClassificationProvider(lambda *_: runtime, limits=limits)
    assert provider._call("system", {"messages": []}, model="test", region="test",
                          timeout=10, stopped=NoWait())
    assert len(attempts) == 3 and len(limits.attempts) == 3 and runtime.closed
    assert code in caplog.text and "secret@example.test" not in caplog.text


def test_flow_stream_throttle_retries_and_closes_each_attempt(caplog):
    entry = target()
    resources = []
    attempts = []

    def factory(name, _):
        if name == "bedrock-agent":
            client = Control(entry)
        else:
            attempts.append(1)
            stream_events = ([{"throttlingException": {"message": "private mail"}}]
                             if len(attempts) < 3 else events())
            client = FlowRuntime(Stream(stream_events))
        resources.append(client)
        return client

    provider = ClassificationProvider(flow_factory=factory)
    assert provider._call("", {"messages": []}, model="test", region="test", timeout=10,
                          stopped=NoWait(), flow=entry)
    assert len(attempts) == 3 and all(c.closed for c in resources)
    assert all(c.stream.closed for c in resources if isinstance(c, FlowRuntime))
    assert "InvokeFlowStream" in caplog.text and "private mail" not in caplog.text


async def test_control_plane_access_denied_does_not_retry(caplog):
    attempts = []

    def factory(*args):
        attempts.append(1)
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "private"}},
                          "GetPrompt")

    with pytest.raises(ApiError) as error:
        await generate(ClassificationProvider(flow_factory=factory), target())
    assert error.value.code == "classification_provider_unavailable"
    assert len(attempts) == 1 and "GetPrompt" in caplog.text and "private" not in caplog.text


def test_retry_budget_exhaustion_returns_429_and_keeps_slot_cleanup():
    runtime = Runtime()

    def throttle(**kwargs):
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")

    runtime.converse = throttle
    provider = ClassificationProvider(lambda *_: runtime, limits=ClassificationLimits(1, 1))
    with pytest.raises(ApiError) as error:
        provider._call("", {"messages": []}, model="test", region="test", timeout=10,
                       stopped=NoWait())
    assert error.value.code == "classification_busy"
    assert int(error.value.headers["Retry-After"]) > 0 and runtime.closed
    assert provider.slots.acquire(blocking=False)
    provider.slots.release()


async def test_exhausted_throttles_are_bounded(monkeypatch):
    runtime = Runtime()
    attempts = []

    def throttle(**kwargs):
        attempts.append(1)
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")

    runtime.converse = throttle
    provider = ClassificationProvider(lambda *_: runtime)
    # Use synchronous seam to avoid real backoff in this test.
    with pytest.raises(ApiError) as error:
        provider._call("", {"messages": []}, model="test", region="test", timeout=10,
                       stopped=NoWait())
    assert len(attempts) == 3 and runtime.closed
    assert error.value.headers == {"Retry-After": "5"}


def test_cancellation_during_backoff_prevents_new_attempt():
    stopped = threading.Event()
    attempts = []

    def throttle():
        attempts.append(1)
        stopped.set()
        raise ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")

    provider = ClassificationProvider()
    with pytest.raises(ApiError) as error:
        provider._retry(throttle, stopped, float("inf"))
    assert len(attempts) == 1 and error.value.code == "classification_cancelled"
