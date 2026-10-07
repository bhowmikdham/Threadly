"""Speech provider failures must never masquerade as Threadly session failures."""

import base64

import httpx
import pytest

from app.api.routes import voice
from tests.conftest import needs_pg


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "synthetic-provider-key")
    requests = []

    def install(result):
        def respond(request):
            requests.append(request)
            if isinstance(result, Exception):
                raise result
            return result

        client_class = httpx.AsyncClient
        monkeypatch.setattr(
            voice.httpx,
            "AsyncClient",
            lambda: client_class(transport=httpx.MockTransport(respond)),
        )

    return install, requests


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_provider_errors_are_not_session_errors(client, auth_headers, provider, status):
    install, requests = provider
    install(httpx.Response(status, text="private provider diagnostic"))
    response = client.post("/voice/speak", json={"text": "Focus"}, headers=auth_headers(1))
    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "voice_provider_error",
            "message": "Speech is unavailable right now. Try again later.",
            "detail": None,
        }
    }
    assert len(requests) == 1


@pytest.mark.parametrize(
    "error,status,code",
    [
        (httpx.ReadTimeout("private timeout"), 504, "voice_timeout"),
        (httpx.ConnectError("private connection diagnostic"), 503, "voice_unavailable"),
    ],
)
def test_transport_errors_are_bounded(client, auth_headers, provider, error, status, code):
    install, _ = provider
    install(error)
    response = client.post("/voice/speak", json={"text": "Focus"}, headers=auth_headers(1))
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private" not in response.text


def test_missing_configuration_is_not_auth_failure(client, auth_headers, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    response = client.post("/voice/speak", json={"text": "Focus"}, headers=auth_headers(1))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_unavailable"


def test_success_preserves_audio_and_masking(client, auth_headers, provider):
    install, requests = provider
    install(httpx.Response(200, content=b"synthetic-mp3"))
    response = client.post(
        "/voice/speak",
        json={"text": "Email alex@example.test"},
        headers=auth_headers(1),
    )
    assert response.status_code == 200
    assert response.json() == {"audio": base64.b64encode(b"synthetic-mp3").decode()}
    assert b"alex@example.test" not in requests[0].content
    assert requests[0].headers["xi-api-key"] == "synthetic-provider-key"


@pytest.mark.parametrize("authorization", [None, "Bearer invalid"])
def test_actual_auth_failure_remains_401(client, provider, authorization):
    install, requests = provider
    install(httpx.Response(200, content=b"must-not-be-used"))
    response = client.post(
        "/voice/speak",
        json={"text": "Focus"},
        headers={"Authorization": authorization} if authorization else {},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert requests == []


@needs_pg
@pytest.mark.parametrize("status", [401, 403])
async def test_provider_rejection_preserves_real_session_and_google_connection(
    db_sessionmaker,
    secure_db_client,
    provider,
    status,
):
    from app.auth import service
    from app.db.models import User
    from tests.test_revocable_sessions import bearer, login

    access, user = await login(db_sessionmaker, sub="voice", email="voice@example.test")
    before = (user.google_connected, user.google_account_version, user.threadly_session_version)
    install, requests = provider
    install(httpx.Response(status, text="private provider diagnostic"))
    response = secure_db_client.post(
        "/voice/speak",
        json={"text": "Focus"},
        headers=bearer(access),
    )
    assert response.status_code == 502
    assert (
        secure_db_client.get("/assistant/capabilities", headers=bearer(access)).status_code == 200
    )
    assert (
        secure_db_client.post(
            "/auth/refresh",
            headers=bearer(service.refresh_token_for(access)),
        ).status_code
        == 200
    )
    async with db_sessionmaker.begin() as session:
        current = await session.get(User, user.id)
        assert (
            current.google_connected,
            current.google_account_version,
            current.threadly_session_version,
        ) == before
        # A genuinely revoked synthetic session must still fail before provider egress.
        current.threadly_session_version += 1
    revoked = secure_db_client.post(
        "/voice/speak",
        json={"text": "Focus"},
        headers=bearer(access),
    )
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "reauth_required"
    assert len(requests) == 1
