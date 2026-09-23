"""The fixed local callback requires BOTH a feature flag and exact allowlisting."""

import pytest

from app.api.errors import ApiError
from app.auth.flow import validate_redirect
from app.config import Settings, get_settings

CALLBACK = "http://127.0.0.1:8765/oauth/callback"


def test_loopback_exception_off_by_default():
    assert Settings(_env_file=None).google_allow_loopback_test_callback is False


@pytest.mark.parametrize("enabled,allowlisted", [(False, True), (True, False), (False, False)])
def test_loopback_requires_flag_and_allowlist(monkeypatch, enabled, allowlisted):
    s = get_settings()
    monkeypatch.setattr(s, "google_allow_loopback_test_callback", enabled)
    monkeypatch.setattr(s, "google_redirect_uri", "")
    monkeypatch.setattr(s, "google_redirect_uri_allowlist", CALLBACK if allowlisted else "")
    with pytest.raises(ApiError) as error:
        validate_redirect(CALLBACK)
    assert error.value.code == "invalid_redirect_uri"


def test_exact_callback_can_be_opted_in_even_on_domain_free_staging(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "prod")
    monkeypatch.setattr(s, "google_allow_loopback_test_callback", True)
    monkeypatch.setattr(s, "google_redirect_uri", CALLBACK)
    validate_redirect(CALLBACK)


@pytest.mark.parametrize(
    "uri",
    [
        "http://localhost:8765/oauth/callback",
        "http://127.0.0.2:8765/oauth/callback",
        "http://[::1]:8765/oauth/callback",
        "http://127.0.0.1:8766/oauth/callback",
        "http://127.0.0.1:8765/oauth/callback/",
        "http://127.0.0.1:8765/oauth/callback?next=https://attacker.example",
        "http://127.0.0.1:8765/oauth/callback#fragment",
        "http://127.0.0.1:8765/other",
        "http://user@127.0.0.1:8765/oauth/callback",
        "http://127.0.0.1.attacker.example:8765/oauth/callback",
        "http://2130706433:8765/oauth/callback",
        "http://example.com:8765/oauth/callback",
        "http://192.0.2.10:8765/oauth/callback",
        "http://127.0.0.1:8765/oauth/callback\n",
    ],
)
def test_other_http_urls_rejected_even_when_allowlisted(monkeypatch, uri):
    s = get_settings()
    monkeypatch.setattr(s, "google_allow_loopback_test_callback", True)
    monkeypatch.setattr(s, "google_redirect_uri", uri)
    with pytest.raises(ApiError):
        validate_redirect(uri)


@pytest.mark.parametrize("enabled", [False, True])
def test_existing_https_callback_unchanged(monkeypatch, enabled):
    s = get_settings()
    monkeypatch.setattr(s, "google_allow_loopback_test_callback", enabled)
    monkeypatch.setattr(s, "google_redirect_uri", "https://ext.chromiumapp.org/")
    validate_redirect("https://ext.chromiumapp.org/")
