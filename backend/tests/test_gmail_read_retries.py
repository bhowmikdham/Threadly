"""Only documented read throttles retry; permission failures remain explicit."""
import gzip
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from app.mail.live import provider_error
from app.sync import gmail


@pytest.mark.parametrize("status", [200, 403])
async def test_compressed_response_is_decoded_once_and_keeps_retry_after(status):
    body = ({"historyId": "123"} if status == 200 else
            {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}})

    def handler(request):
        return httpx.Response(status, stream=httpx.ByteStream(gzip.compress(
            json.dumps(body).encode())), headers={
                "Content-Encoding": "gzip", "Retry-After": "60"})

    client = gmail.GmailClient("token", transport=httpx.MockTransport(handler))
    if status == 200:
        assert await client.get_profile() == body
    else:
        with pytest.raises(gmail.GmailError) as error:
            await client.get_profile()
        mapped = provider_error(error.value)
        assert mapped.code == "gmail_rate_limited"
        assert mapped.status == 429 and mapped.headers == {"Retry-After": "60"}


@pytest.mark.parametrize("status,reason", [(403, "rateLimitExceeded"),
    (403, "userRateLimitExceeded"), (429, "unknown"), (503, "backendError")])
async def test_retry_recovers_read(status, reason, monkeypatch, caplog):
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(status, json={"error": {"message": "private@example.test",
                "errors": [{"reason": reason}]}}, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"historyId": "123"})

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(gmail.asyncio, "sleep", sleep)
    client = gmail.GmailClient("token", transport=httpx.MockTransport(handler))
    assert await client.get_profile() == {"historyId": "123"}
    assert len(calls) == 3 and len(sleeps) == 2 and all(2 <= d < 4 for d in sleeps)
    assert "private@example.test" not in caplog.text and "token" not in caplog.text


@pytest.mark.parametrize("reason", ["domainPolicy", "insufficientPermissions", "forbidden",
                                    "private@example.test", "unknown"])
async def test_permission_or_unknown_403_never_retries(reason):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, json={"error": {"errors": [{"reason": reason}]}})

    client = gmail.GmailClient("token", transport=httpx.MockTransport(handler))
    with pytest.raises(gmail.GmailError) as error:
        await client.get_profile()
    assert len(calls) == 1
    assert provider_error(error.value).code == "gmail_access_denied"
    assert "@" not in error.value.reason


@pytest.mark.parametrize("body", [{"error": "oops"}, {"error": {"errors": None}}, [],
    {"error": {"errors": [{"reason": "rateLimitExceeded"}, {"reason": "forbidden"}]}}])
async def test_malformed_or_mixed_reason_is_not_assumed_throttling(body):
    client = gmail.GmailClient("token", transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json=body)))
    with pytest.raises(gmail.GmailError) as error:
        await client.get_profile()
    assert provider_error(error.value).code == "gmail_access_denied"


async def test_exhausted_throttle_returns_retry_after(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, json={"error": {"errors": [
            {"reason": "userRateLimitExceeded"}]}})

    async def sleep(delay):
        pass

    monkeypatch.setattr(gmail.asyncio, "sleep", sleep)
    client = gmail.GmailClient("token", transport=httpx.MockTransport(handler))
    with pytest.raises(gmail.GmailError) as error:
        await client.get_profile()
    mapped = provider_error(error.value)
    assert len(calls) == 3 and mapped.status == 429
    assert mapped.code == "gmail_rate_limited" and mapped.headers == {"Retry-After": "5"}


async def test_long_cooldown_is_returned_without_waiting():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={}, headers={"Retry-After": "120"})

    client = gmail.GmailClient("token", transport=httpx.MockTransport(handler))
    with pytest.raises(gmail.GmailError) as error:
        await client.get_profile()
    assert len(calls) == 1
    assert provider_error(error.value).headers == {"Retry-After": "120"}


def test_retry_after_http_date_and_daily_quota():
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=60))
    assert 59 <= gmail.retry_after_seconds(future) <= 60
    for value in (None, "garbage", "NaN", "inf"):
        assert gmail.retry_after_seconds(value) is None
    mapped = provider_error(gmail.GmailError("safe", 403, reason="dailyLimitExceeded"))
    assert mapped.status == 503 and mapped.code == "gmail_quota_exceeded"
