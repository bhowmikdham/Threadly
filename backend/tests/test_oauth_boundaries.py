"""API/real-Postgres OAuth boundaries with synthetic Google transport."""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select, update

from app.api.errors import ApiError
from app.auth import crypto, flow, google, service
from app.config import get_settings
from app.db.models import GoogleOAuthSession, User
from tests.conftest import needs_pg
from tests.test_auth_flow import google_transport

pytestmark = needs_pg
REDIRECT = "https://ext.chromiumapp.org/"
VERIFIER = "a" * 64
READ = "https://www.googleapis.com/auth/gmail.readonly"
SEND = "https://www.googleapis.com/auth/gmail.send"


@pytest.fixture
def configured(db_sessionmaker, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "synthetic-client")
    monkeypatch.setattr(settings, "google_client_secret", "synthetic-secret")
    monkeypatch.setattr(settings, "google_redirect_uri_allowlist", REDIRECT)
    monkeypatch.setattr(flow, "get_session_factory", lambda: db_sessionmaker)
    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    original = service.exchange_code

    async def exchange(*args, **kwargs):
        return await original(*args, **kwargs, transport=google_transport(scopes=READ))

    monkeypatch.setattr(service, "exchange_code", exchange)
    return original


def begin(client, headers=None):
    path = "/auth/google/reconnect" if headers else "/auth/google/begin"
    response = client.post(
        path,
        headers=headers,
        json={"redirect_uri": REDIRECT, "code_challenge": flow.challenge(VERIFIER)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def exchange_body(start, **changes):
    return {
        "redirect_uri": REDIRECT,
        "code": "synthetic-code",
        "state": start["state"],
        "code_verifier": VERIFIER,
        **changes,
    }


def test_route_state_pkce_replay_and_capabilities(db_client, configured):
    start = begin(db_client)
    params = parse_qs(urlsplit(start["authorization_url"]).query)
    assert params["code_challenge"] == [flow.challenge(VERIFIER)]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid email profile " + READ]
    bad = db_client.post("/auth/google/exchange", json=exchange_body(start, code_verifier="b" * 64))
    assert bad.status_code == 400
    result = db_client.post("/auth/google/exchange", json=exchange_body(start))
    assert result.status_code == 200, result.text
    headers = {"Authorization": "Bearer " + result.json()["jwt"]}
    replay = db_client.post("/auth/google/exchange", json=exchange_body(start))
    assert replay.status_code == 400
    caps = db_client.get("/assistant/capabilities", headers=headers).json()
    assert caps["account"]["connected"] is True
    by_id = {c["id"]: c for c in caps["capabilities"]}
    assert by_id["gmail_read"]["ready"]
    assert not any(by_id[c]["ready"] for c in ["gmail_send", "calendar_read", "calendar_write"])
    assert "synthetic-secret" not in str(caps)
    assert db_client.post("/auth/google/disconnect", headers=headers).status_code == 200
    assert not db_client.get("/assistant/capabilities", headers=headers).json()["account"][
        "connected"
    ]
    assert db_client.get("/assistant/capabilities").status_code == 401


async def test_state_expiry_concurrent_consume_and_callback_binding(db_sessionmaker, configured):
    async with db_sessionmaker.begin() as session:
        start = await flow.begin(session, REDIRECT, flow.challenge(VERIFIER))
    outcomes = await asyncio.gather(
        *[flow.consume(start["state"], REDIRECT, VERIFIER) for _ in range(2)],
        return_exceptions=True,
    )
    assert sum(isinstance(v, tuple) for v in outcomes) == 1
    assert sum(isinstance(v, ApiError) for v in outcomes) == 1
    async with db_sessionmaker.begin() as session:
        start = await flow.begin(session, REDIRECT, flow.challenge(VERIFIER))
        await session.execute(
            update(GoogleOAuthSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    with pytest.raises(ApiError):
        await flow.consume(start["state"], REDIRECT, VERIFIER)
    with pytest.raises(ApiError):
        await flow.consume(start["state"], "https://attacker.example/", VERIFIER)


async def test_reconnect_same_account_and_version_fence(db_sessionmaker, configured):
    async with db_sessionmaker.begin() as session:
        _, user = await configured(
            session, "code", REDIRECT, transport=google_transport(scopes=READ)
        )
        owner, version = user.id, user.google_account_version
    async with db_sessionmaker() as session:
        with pytest.raises(ApiError) as failed:
            await configured(
                session,
                "code",
                REDIRECT,
                expected_user_id=owner,
                expected_version=version,
                transport=google_transport(sub="different"),
            )
        assert failed.value.code == "google_account_mismatch"
    assert await service.disconnect_google_account(owner)
    async with db_sessionmaker() as session:
        with pytest.raises(ApiError) as failed:
            await configured(
                session,
                "code",
                REDIRECT,
                expected_user_id=owner,
                expected_version=version,
                transport=google_transport(),
            )
        assert failed.value.code == "google_connection_changed"
    async with db_sessionmaker.begin() as session:
        row = await session.get(User, owner)
        _, row = await configured(
            session,
            "code",
            REDIRECT,
            expected_user_id=owner,
            expected_version=row.google_account_version,
            transport=google_transport(scopes=READ),
        )
        assert row.google_connected


async def expired_account(factory, original):
    async with factory.begin() as session:
        _, row = await original(
            session, "code", REDIRECT, transport=google_transport(scopes=READ + " " + SEND)
        )
        row.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        return row.id


async def test_refresh_network_disconnect_race_and_cached_user(db_sessionmaker, configured):
    owner = await expired_account(db_sessionmaker, configured)
    entered, released = asyncio.Event(), asyncio.Event()

    async def response(request):
        entered.set()
        await released.wait()
        return httpx.Response(200, json={"access_token": "late-secret", "expires_in": 3600})

    async with db_sessionmaker() as caller:
        user = await caller.get(User, owner)
        pending = asyncio.create_task(
            service.get_valid_access_token(caller, user, transport=httpx.MockTransport(response))
        )
        await entered.wait()
        assert await service.disconnect_google_account(owner)
        released.set()
        with pytest.raises(ApiError) as failed:
            await pending
        assert failed.value.code == "google_connection_changed"
        # Even a cached ORM user cannot return the old token after disconnect.
        with pytest.raises(ApiError) as failed:
            await service.get_valid_access_token(caller, user)
        assert failed.value.code == "reauth_required"
    async with db_sessionmaker() as session:
        row = await session.get(User, owner)
        assert row.access_token_enc is None and row.refresh_token_enc is None


@pytest.mark.parametrize(
    "error,status,disconnected",
    [
        ("invalid_grant", 400, True),
        ("invalid_client", 400, False),
        ("server_error", 503, False),
    ],
)
async def test_refresh_error_classification_and_sanitization(
    db_sessionmaker, configured, caplog, error, status, disconnected
):
    owner = await expired_account(db_sessionmaker, configured)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            status, json={"error": error, "error_description": "SYNTHETIC_PRIVATE_TOKEN"}
        )
    )
    async with db_sessionmaker() as session:
        with pytest.raises(ApiError) as failed:
            await service.get_valid_access_token(
                session, await session.get(User, owner), transport=transport
            )
        assert failed.value.code == (
            "reauth_required" if disconnected else "google_token_unavailable"
        )
        assert "SYNTHETIC_PRIVATE_TOKEN" not in str(failed.value)
    async with db_sessionmaker() as session:
        assert (await session.get(User, owner)).google_connected is not disconnected
    assert "SYNTHETIC_PRIVATE_TOKEN" not in caplog.text


async def test_refresh_reduction_and_independent_versions(db_sessionmaker, configured):
    owner = await expired_account(db_sessionmaker, configured)
    async with db_sessionmaker() as session:
        user = await session.get(User, owner)
        original_version = user.google_account_version
        await service.get_valid_access_token(session, user, transport=google_transport(None))
    async with db_sessionmaker.begin() as session:
        row = await session.get(User, owner)
        assert row.google_account_version == original_version
        assert set(row.google_scopes) == {READ, SEND}
        assert crypto.decrypt_token(row.refresh_token_enc) == "rt-1"
        row.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    async with db_sessionmaker() as session:
        await service.get_valid_access_token(
            session, await session.get(User, owner), transport=google_transport(None, scopes=READ)
        )
    async with db_sessionmaker() as session:
        row = await session.get(User, owner)
        assert row.google_account_version == original_version + 1
        assert row.google_scopes == [READ]


async def test_exchange_rollback_and_simultaneous_first_login(db_sessionmaker, configured):
    async with db_sessionmaker() as session:
        await configured(session, "code", REDIRECT, transport=google_transport())
        await session.rollback()
    async with db_sessionmaker() as session:
        assert await session.scalar(select(User)) is None

    async def login():
        async with db_sessionmaker.begin() as session:
            _, user = await configured(session, "code", REDIRECT, transport=google_transport())
            return user.id

    assert len(set(await asyncio.gather(login(), login()))) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "", "expires_in": 3600},
        {"access_token": "token", "expires_in": True},
        {"access_token": "token", "expires_in": 3600, "refresh_token": {}},
        {"access_token": "token", "expires_in": 3600, "scope": ["invented"]},
    ],
)
async def test_malformed_tokens_are_sanitized(payload):
    with pytest.raises(google.GoogleAuthError):
        await google.exchange_code(
            "code",
            REDIRECT,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)),
        )


async def test_transport_failure_and_unverified_identity_are_sanitized():
    def fail(request):
        raise httpx.ConnectError("SYNTHETIC_PRIVATE_TOKEN", request=request)

    with pytest.raises(google.GoogleAuthError) as error:
        await google.exchange_code("code", REDIRECT, transport=httpx.MockTransport(fail))
    assert "SYNTHETIC_PRIVATE_TOKEN" not in str(error.value)
    with pytest.raises(google.GoogleAuthError):
        await google.fetch_userinfo("token", transport=google_transport(email_verified=False))


def test_failed_provider_exchange_consumes_state_and_legacy_input_rejected(
    db_client, configured, monkeypatch, caplog
):
    start = begin(db_client)

    async def failure(*args, **kwargs):
        raise ApiError(401, "oauth_exchange_failed", "Google authorization request failed.")

    monkeypatch.setattr(service, "exchange_code", failure)
    response = db_client.post("/auth/google/exchange", json=exchange_body(start))
    assert response.status_code == 401
    replay = db_client.post("/auth/google/exchange", json=exchange_body(start))
    assert replay.json()["error"]["code"] == "oauth_state_invalid"
    legacy = db_client.post(
        "/auth/google/exchange", json={"code": "PRIVATE", "redirect_uri": REDIRECT}
    )
    assert legacy.status_code == 422 and "PRIVATE" not in legacy.text
    assert "PRIVATE" not in caplog.text


async def test_concurrent_refreshes_one_winner(db_sessionmaker, configured):
    owner = await expired_account(db_sessionmaker, configured)
    all_entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def provider(request):
        nonlocal count
        count += 1
        if count == 2:
            all_entered.set()
        await release.wait()
        return httpx.Response(200, json={"access_token": "winner", "expires_in": 3600})

    async def refresh():
        async with db_sessionmaker() as session:
            return await service.get_valid_access_token(
                session, await session.get(User, owner), transport=httpx.MockTransport(provider)
            )

    pending = [asyncio.create_task(refresh()) for _ in range(2)]
    await all_entered.wait()
    release.set()
    results = await asyncio.gather(*pending, return_exceptions=True)
    assert sum(value == "winner" for value in results) == 1
    assert sum(isinstance(value, ApiError) for value in results) == 1


def test_invalid_callback_and_subject_fields_do_not_start_login(db_client, configured):
    for callback in ["https://[broken", "http://ext.chromiumapp.org/", REDIRECT + "#fragment"]:
        response = db_client.post(
            "/auth/google/begin",
            json={"redirect_uri": callback, "code_challenge": flow.challenge(VERIFIER)},
        )
        assert response.status_code == 400
    response = db_client.post(
        "/auth/google/begin",
        json={"redirect_uri": REDIRECT, "code_challenge": flow.challenge(VERIFIER), "user_id": 2},
    )
    assert response.status_code == 422


async def test_token_retrieval_cannot_commit_pending_action(db_sessionmaker, configured):
    from app.actions.service import propose
    from app.db.models import AssistantAction
    from tests.test_action_storage import candidate
    from tests.test_draft_review import generated

    owner = await expired_account(db_sessionmaker, configured)
    artifact = await generated(db_sessionmaker, owner)
    async with db_sessionmaker() as caller:
        await propose(caller, owner, artifact.id, **candidate())
        await service.get_valid_access_token(
            caller, await caller.get(User, owner), transport=google_transport(None)
        )
        await caller.rollback()
    async with db_sessionmaker() as session:
        assert await session.scalar(select(AssistantAction)) is None
        assert (await session.get(User, owner)).google_token_version == 2
