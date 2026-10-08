"""Admission and rolling attempt limits without wall-clock sleeps."""
# ruff: noqa: F811 - shared pytest fixtures
import asyncio

import pytest

from app.api.errors import ApiError
from app.classification import service
from app.classification.limits import ClassificationLimits
from tests.test_classification import configured, invoke, pipeline  # noqa: F401


def test_rolling_limit_reports_precise_retry_and_recovers():
    now = [0.0]
    limits = ClassificationLimits(2, 2, clock=lambda: now[0])
    limits.check_rate(consume=True)
    now[0] = 10
    limits.check_rate(consume=True)
    now[0] = 25.1
    with pytest.raises(ApiError) as error:
        limits.check_rate(consume=True)
    assert error.value.headers == {"Retry-After": "35"}
    now[0] = 60
    limits.check_rate(consume=True)
    assert list(limits.attempts) == [10, 60]


async def test_busy_requests_do_not_fetch_gmail(pipeline, monkeypatch):
    _, provider, reads = pipeline
    limits = ClassificationLimits(1, 8)
    monkeypatch.setattr(service, "get_limits", lambda: limits)
    with limits.admission():
        with pytest.raises(ApiError) as error:
            await invoke()
    assert error.value.code == "classification_busy"
    assert not reads and not provider.calls
    assert (await invoke()).status == "classified"


async def test_rate_limit_rejects_before_gmail(pipeline, monkeypatch):
    _, provider, reads = pipeline
    limits = ClassificationLimits(2, 1)
    limits.check_rate(consume=True)
    monkeypatch.setattr(service, "get_limits", lambda: limits)
    with pytest.raises(ApiError) as error:
        await invoke()
    assert error.value.headers == {"Retry-After": "60"}
    assert not reads and not provider.calls


async def test_cancelled_gmail_read_releases_admission(pipeline, monkeypatch):
    limits = ClassificationLimits(1, 8)
    monkeypatch.setattr(service, "get_limits", lambda: limits)
    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service.live, "thread", blocked)
    task = asyncio.create_task(invoke())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with limits.admission():
        pass
