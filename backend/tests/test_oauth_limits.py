"""Public OAuth ingress limits; provider calls are never made in these tests."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from app.api.errors import ApiError
from app.auth import flow, limits
from app.config import get_settings
from app.db.models import GoogleOAuthSession, OAuthRateLimit
from app.main import create_app
from tests.conftest import needs_pg


def _body():
    return {
        "redirect_uri": "https://ext.chromiumapp.org/",
        "code_challenge": flow.challenge("a" * 64),
    }


def _request(address):
    return Request({"type": "http", "client": (address, 1234), "headers": []})


def test_oauth_body_limit_precedes_schema_validation_and_database(monkeypatch):
    async def no_db(*_args):
        return None

    monkeypatch.setattr(limits, "enforce", no_db)
    client = TestClient(create_app(), raise_server_exceptions=False)
    for content in (b"x" * 8193, iter([b"x" * 4096, b"x" * 4097])):
        result = client.post(
            "/auth/google/begin", content=content, headers={"Content-Type": "application/json"}
        )
        assert result.status_code == 413
        assert result.json()["error"]["code"] == "request_too_large"
        assert result.headers["cache-control"] == "no-store"

    # The route still gives ordinary Pydantic envelopes for bounded bodies.
    result = client.post("/auth/google/begin", json={"extra": "value"})
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "validation_error"


@needs_pg
def test_begin_limit_shared_by_requests_and_spoofed_forwarding_ignored(
    db_client, db_sessionmaker, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "synthetic-client")
    monkeypatch.setattr(settings, "google_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "google_redirect_uri_allowlist", _body()["redirect_uri"])
    monkeypatch.setattr(settings, "oauth_begin_max_per_minute", 2)
    monkeypatch.setattr(limits, "get_session_factory", lambda: db_sessionmaker)

    invalid = db_client.post(
        "/auth/google/begin", json={"extra": "value"}, headers={"X-Forwarded-For": "203.0.113.1"}
    )
    assert invalid.status_code == 422  # even invalid attempts spend the shared allowance
    response = db_client.post(
        "/auth/google/begin", json=_body(), headers={"X-Forwarded-For": "198.51.100.42"}
    )
    assert response.status_code == 200, response.text
    denied = db_client.post(
        "/auth/google/begin", json=_body(), headers={"X-Forwarded-For": "192.0.2.9"}
    )
    assert denied.status_code == 429
    assert denied.json()["error"]["code"] == "oauth_rate_limited"
    assert 1 <= int(denied.headers["Retry-After"]) <= 60
    assert denied.headers["cache-control"] == "no-store"

    async def inspect():
        async with db_sessionmaker() as session:
            states = (await session.scalars(select(GoogleOAuthSession))).all()
            keys = (await session.scalars(select(OAuthRateLimit.bucket_key))).all()
            assert len(states) == 1  # invalid and denied requests created no state
            assert len(keys) == 2  # peer + global, both keyed digests
            assert all(len(key) == 64 and "testclient" not in key for key in keys)

    asyncio.run(inspect())


@needs_pg
def test_exchange_limit_precedes_one_use_state_consumption(db_client, db_sessionmaker, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "synthetic-client")
    monkeypatch.setattr(settings, "google_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "google_redirect_uri_allowlist", _body()["redirect_uri"])
    monkeypatch.setattr(settings, "oauth_exchange_max_per_minute", 1)
    monkeypatch.setattr(limits, "get_session_factory", lambda: db_sessionmaker)
    begin = db_client.post("/auth/google/begin", json=_body())
    assert begin.status_code == 200
    state = begin.json()["state"]
    asyncio.run(limits.enforce(_request("testclient"), "exchange"))
    denied = db_client.post(
        "/auth/google/exchange",
        json={
            "code": "synthetic-code",
            "redirect_uri": _body()["redirect_uri"],
            "state": state,
            "code_verifier": "a" * 64,
        },
    )
    assert denied.status_code == 429

    async def inspect():
        import hashlib

        async with db_sessionmaker() as session:
            state_hash = hashlib.sha256(state.encode()).hexdigest()
            saved = await session.get(GoogleOAuthSession, state_hash)
            assert saved.consumed_at is None

    asyncio.run(inspect())


@needs_pg
def test_new_begin_prunes_expired_state_without_waiting_a_day(
    db_client, db_sessionmaker, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "synthetic-client")
    monkeypatch.setattr(settings, "google_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "google_redirect_uri_allowlist", _body()["redirect_uri"])
    monkeypatch.setattr(limits, "get_session_factory", lambda: db_sessionmaker)
    first = db_client.post("/auth/google/begin", json=_body())
    assert first.status_code == 200

    async def expire():
        async with db_sessionmaker.begin() as session:
            expired_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.execute(update(GoogleOAuthSession).values(expires_at=expired_at))

    asyncio.run(expire())
    second = db_client.post("/auth/google/begin", json=_body())
    assert second.status_code == 200

    async def inspect():
        async with db_sessionmaker() as session:
            states = (await session.scalars(select(GoogleOAuthSession))).all()
            assert len(states) == 1

    asyncio.run(inspect())


@needs_pg
async def test_rate_counters_atomic_across_sessions_and_reset(db_sessionmaker, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "oauth_exchange_max_per_minute", 2)
    monkeypatch.setattr(limits, "get_session_factory", lambda: db_sessionmaker)

    outcomes = await asyncio.gather(
        *(limits.enforce(_request("192.0.2.44"), "exchange") for _ in range(7)),
        return_exceptions=True,
    )
    assert sum(result is None for result in outcomes) == 2
    assert sum(getattr(result, "code", None) == "oauth_rate_limited" for result in outcomes) == 5

    # A different peer has a separate bucket. A matured window allows retry.
    await limits.enforce(_request("192.0.2.45"), "exchange")
    async with db_sessionmaker.begin() as session:
        bucket = await session.get(OAuthRateLimit, limits._bucket_key("exchange", "192.0.2.44"))
        bucket.window_start -= limits._WINDOW
    await limits.enforce(_request("192.0.2.44"), "exchange")


@needs_pg
async def test_global_limit_across_different_peers(db_sessionmaker, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "oauth_global_max_per_minute", 2)
    monkeypatch.setattr(limits, "get_session_factory", lambda: db_sessionmaker)
    await limits.enforce(_request("192.0.2.1"), "begin")
    await limits.enforce(_request("192.0.2.2"), "begin")
    with pytest.raises(ApiError) as denied:
        await limits.enforce(_request("192.0.2.3"), "begin")
    assert getattr(denied.value, "code", None) == "oauth_rate_limited"


@needs_pg
def test_limiter_database_failure_closes_oauth_before_state_creation(db_client, monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise SQLAlchemyError("synthetic unavailable")

    monkeypatch.setattr(limits, "_increment", unavailable)
    response = db_client.post("/auth/google/begin", json=_body())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "oauth_unavailable"
    assert "synthetic" not in response.text
