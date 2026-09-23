from types import SimpleNamespace

import httpx
import pytest


def _user(*, scopes, connected=True, email_verified=True):
    return SimpleNamespace(
        id=1,
        google_scopes=scopes,
        google_connected=connected,
        google_email_verified=email_verified,
        google_identity={"sub": "google-sub"},
        google_account_version=1,
        access_token_expires_at=None,
        refresh_token_enc=b"encrypted",
        access_token_enc=None,
    )


def _by_id(snapshot):
    return {item["id"]: item for item in snapshot["capabilities"]}


def test_unknown_and_reduced_grants_never_advertise_unavailable_writes():
    from app.capabilities.service import build_capabilities

    unknown = _by_id(build_capabilities(_user(scopes=None)))
    assert unknown["gmail_read"]["status"] == "scope_unknown"
    assert unknown["gmail_send"]["ready"] is False
    assert unknown["calendar_write"]["ready"] is False

    reduced = _by_id(
        build_capabilities(_user(scopes=["https://www.googleapis.com/auth/gmail.readonly"]))
    )
    assert reduced["gmail_read"]["ready"] is True
    assert reduced["gmail_send"]["ready"] is False
    assert reduced["calendar_write"]["ready"] is False


@pytest.mark.asyncio
async def test_pkce_is_forwarded_and_provider_tokens_do_not_escape_errors(caplog):
    from app.auth import google

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(item.split("=", 1) for item in request.content.decode().split("&")))
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "access_token": "synthetic-secret-token"},
        )

    with pytest.raises(google.GoogleAuthError) as error:
        await google.exchange_code(
            "code",
            "https://ext.chromiumapp.org/",
            code_verifier="a" * 43,
            transport=httpx.MockTransport(handler),
        )
    assert captured["code_verifier"] == "a" * 43
    assert "synthetic-secret-token" not in str(error.value)
    assert "synthetic-secret-token" not in caplog.text


def test_invalid_redirect_and_spoofed_subject_are_rejected(monkeypatch):
    from app.api.errors import ApiError
    from app.api.routes import auth
    from app.auth import flow

    monkeypatch.setattr(
        flow,
        "get_settings",
        lambda: SimpleNamespace(
            google_allow_loopback_test_callback=False,
            google_redirect_uri_allowlist_values={"https://ext.chromiumapp.org/"}
        ),
    )
    with pytest.raises(ApiError) as error:
        flow.validate_redirect("https://untrusted.example/callback")
    assert error.value.code == "invalid_redirect_uri"
    with pytest.raises(ValueError):
        auth.ExchangeIn.model_validate(
            {
                "code": "code",
                "redirect_uri": "https://ext.chromiumapp.org/",
                "sub": "attacker-controlled-subject",
            }
        )
