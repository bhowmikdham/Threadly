"""Local helper boundaries and a browser->callback->exchange round trip; no Google calls."""

import hashlib
import http.client
import json
import threading
from http.server import HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from tools import google_local_login as helper

STATE = "a" * 43
SECRET = "synthetic-private-code"
SESSION = {"jwt": "synthetic-private-jwt", "user": {"id": 7}}


@pytest.mark.parametrize(
    "query",
    [
        {"state": "wrong", "code": SECRET},
        {"state": "é" * 43, "code": SECRET},
        {"state": [STATE, STATE], "code": SECRET},
        {"state": STATE, "code": [SECRET, SECRET]},
        {"state": STATE, "code": ""},
        {"state": STATE},
    ],
)
def test_invalid_callback_rejected(query):
    with pytest.raises(helper.LoginError):
        helper.callback_code("/oauth/callback?" + urlencode(query, doseq=True), STATE)


def test_callback_denial_requires_matching_state():
    with pytest.raises(PermissionError):
        helper.callback_code(
            "/oauth/callback?"
            + urlencode(
                {
                    "state": STATE,
                    "error": "access_denied",
                }
            ),
            STATE,
        )
    with pytest.raises(helper.LoginError):
        helper.callback_code("/oauth/callback?error=access_denied&state=wrong", STATE)


def test_private_session_permissions_and_symlink_rejection(tmp_path):
    path = tmp_path / "private" / "session.json"
    helper.save_session(path, SESSION)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert helper.read_session(path) == SESSION
    path.chmod(0o644)
    with pytest.raises(helper.LoginError):
        helper.read_session(path)
    path.unlink()
    target = tmp_path / "do-not-change"
    target.write_text("unchanged")
    path.symlink_to(target)
    with pytest.raises(helper.LoginError):
        helper.save_session(path, SESSION)
    assert target.read_text() == "unchanged"


def test_helper_browser_round_trip_and_calendar_reconnect(tmp_path, monkeypatch, capsys):
    path = tmp_path / "private" / "session.json"
    servers, browsers, calls = [], [], []
    challenges = []

    def server_factory(address, handler):
        assert address == ("127.0.0.1", 8765)
        server = HTTPServer(("127.0.0.1", 0), handler)
        servers.append(server)
        return server

    def browser(url):
        params = parse_qs(urlsplit(url).query)
        assert params["state"] == [STATE]

        def visit():
            connection = http.client.HTTPConnection(*servers[-1].server_address, timeout=5)
            connection.request(
                "GET",
                "/oauth/callback?"
                + urlencode(
                    {
                        "state": STATE,
                        "code": SECRET,
                    }
                ),
                headers={"Host": "127.0.0.1:8765"},
            )
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("Cache-Control") == "no-store"
            assert SECRET not in response.read().decode()
            connection.close()

        thread = threading.Thread(target=visit)
        browsers.append(thread)
        thread.start()
        return True

    def api(endpoint, body=None, jwt=None):
        calls.append(endpoint)
        if endpoint in {"/auth/google/begin", "/auth/google/reconnect"}:
            assert body["redirect_uri"] == helper.CALLBACK
            if endpoint.endswith("reconnect"):
                assert jwt == SESSION["jwt"]
                assert body["capabilities"] == ["calendar_read"]
            else:
                assert jwt is None and "capabilities" not in body
            challenges.append(body["code_challenge"])
            return {
                "state": STATE,
                "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?"
                + urlencode(
                    {
                        "state": STATE,
                        "redirect_uri": helper.CALLBACK,
                        "code_challenge": body["code_challenge"],
                        "code_challenge_method": "S256",
                        "response_type": "code",
                    }
                ),
            }
        if endpoint == "/auth/google/exchange":
            assert body["code"] == SECRET
            assert body["state"] == STATE
            assert body["redirect_uri"] == helper.CALLBACK
            expected = (
                helper.base64.urlsafe_b64encode(
                    hashlib.sha256(body["code_verifier"].encode()).digest()
                )
                .rstrip(b"=")
                .decode()
            )
            assert expected == challenges[-1]
            return SESSION
        assert endpoint == "/assistant/capabilities"
        assert jwt == SESSION["jwt"]
        return {"capabilities": [{"id": "gmail_read", "ready": True}]}

    monkeypatch.setattr(helper, "CallbackServer", server_factory)
    monkeypatch.setattr(helper.webbrowser, "open", browser)
    monkeypatch.setattr(helper, "api_request", api)
    helper.login(path)
    helper.login(path, calendar=True)
    for browser_thread in browsers:
        browser_thread.join(timeout=5)
        assert not browser_thread.is_alive()
    assert challenges[0] != challenges[1]
    assert helper.read_session(path) == SESSION
    assert calls == [
        "/auth/google/begin",
        "/auth/google/exchange",
        "/assistant/capabilities",
        "/auth/google/reconnect",
        "/auth/google/exchange",
        "/assistant/capabilities",
    ]
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert SESSION["jwt"] not in output.out + output.err


def test_http_client_refuses_redirects_and_hides_error_bodies(monkeypatch):
    class Handler(helper.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://attacker.invalid/" + SECRET)
            self.end_headers()
            self.wfile.write(SECRET.encode())

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        monkeypatch.setattr(helper, "API", "http://127.0.0.1:" + str(server.server_port))
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        with pytest.raises(helper.LoginError) as error:
            helper.api_request("/assistant/capabilities", jwt=SESSION["jwt"])
        thread.join(timeout=5)
        assert SECRET not in str(error.value)
        assert "302" in str(error.value)


def test_wrong_authorization_host_rejected():
    with pytest.raises(helper.LoginError):
        helper.validate_start(
            {"state": STATE, "authorization_url": "https://attacker.invalid"}, "x"
        )


def test_malformed_session_does_not_overwrite_existing(tmp_path):
    path = tmp_path / "private" / "session.json"
    helper.save_session(path, SESSION)
    with pytest.raises(helper.LoginError):
        helper.save_session(path, {"jwt": "bad", "user": {"id": "7"}})
    assert json.loads(path.read_text()) == SESSION
