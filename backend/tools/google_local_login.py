#!/usr/bin/env python3
"""Local-only OAuth test client; Google secrets stay on the backend.

Run on the developer laptop with the SSM API tunnel open. Uses only Python's
standard library. No mailbox sync, model request, send or booking is performed.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CALLBACK = "http://127.0.0.1:8765/oauth/callback"
API = "http://127.0.0.1:8000"
SESSION = Path.home() / ".threadly-staging" / "session.json"


class LoginError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an authorization code or session JWT to another endpoint.
        return None


def api_request(path, body=None, jwt=None):
    headers = {"Accept": "application/json"}
    if jwt:
        headers["Authorization"] = "Bearer " + jwt
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = Request(API + path, data=data, headers=headers)
    try:
        # The tunnel must be local; do not route credentials through proxy env vars.
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(131073)
            if len(raw) > 131072:
                raise LoginError("Backend response exceeds the test client's limit.")
            value = json.loads(raw)
    except HTTPError as exc:
        raise LoginError(f"Backend returned HTTP {exc.code}; no response body logged.") from None
    except (URLError, TimeoutError, OSError):
        raise LoginError("Backend unavailable. Check the SSM tunnel to port 8000.") from None
    except (ValueError, UnicodeError):
        raise LoginError("Backend returned invalid JSON.") from None
    if not isinstance(value, dict):
        raise LoginError("Backend returned an invalid object.")
    return value


def validate_session(value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("jwt"), str)
        or not 1 <= len(value["jwt"]) <= 16384
        or not isinstance(value.get("user"), dict)
        or type(value["user"].get("id")) is not int
        or value["user"]["id"] <= 0
    ):
        raise LoginError("Invalid Threadly session; no credentials printed.")
    return value


def private_path(path):
    path = Path(path).expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for candidate in (path.parent, path):
        if candidate.is_symlink():
            raise LoginError("Session paths must not be symlinks.")
        if candidate.exists():
            info = candidate.stat()
            expected = 0o700 if candidate == path.parent else 0o600
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != expected:
                raise LoginError("Session directory/file must be owned by you with mode 700/600.")
            if candidate == path and not candidate.is_file():
                raise LoginError("Session path must be a regular file.")
    return path


def read_session(path):
    path = private_path(path)
    try:
        if path.stat().st_size > 32768:
            raise LoginError("Stored session exceeds the test client's limit.")
        return validate_session(json.loads(path.read_text()))
    except (OSError, ValueError, UnicodeError):
        raise LoginError("No valid saved session; run normal login first.") from None


def save_session(path, session):
    path = private_path(path)
    validate_session(session)
    fd, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(session, handle)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def callback_code(target, state):
    if len(target) > 16384:
        raise LoginError("Invalid callback.")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.path != "/oauth/callback" or parsed.fragment:
        raise LoginError("Invalid callback path.")
    try:
        params = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=30)
    except ValueError:
        raise LoginError("Invalid callback fields.") from None
    states = params.get("state", [])
    if (
        len(states) != 1
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", states[0])
        or not secrets.compare_digest(states[0], state)
    ):
        raise LoginError("Callback state did not match.")
    if "error" in params:
        raise PermissionError("Google consent was not completed. Restart login to retry.")
    codes = params.get("code", [])
    if len(codes) != 1 or not 1 <= len(codes[0]) <= 4096:
        raise LoginError("Invalid callback code.")
    return codes[0]


def validate_start(start, challenge):
    state, url = start.get("state"), start.get("authorization_url")
    if (
        not isinstance(state, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", state)
        or not isinstance(url, str)
    ):
        raise LoginError("Backend returned an invalid login session.")
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)
    expected = {
        "state": state,
        "redirect_uri": CALLBACK,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "response_type": "code",
    }
    if (
        parsed.scheme != "https"
        or parsed.netloc != "accounts.google.com"
        or parsed.path != "/o/oauth2/v2/auth"
        or parsed.fragment
        or any(params.get(key) != [value] for key, value in expected.items())
    ):
        raise LoginError("Backend returned an unexpected authorization URL.")
    return state, url


def callback_handler(state, outcome):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Callback URLs contain authorization codes; never log them.

        def do_GET(self):
            status, message = 400, "Invalid callback. Return to the login started in your terminal."
            try:
                if self.headers.get("Host") != "127.0.0.1:8765" or outcome:
                    raise LoginError("Unexpected callback request.")
                code = callback_code(self.path, state)
                outcome["code"] = code
                status, message = (
                    200,
                    "Authorization received. Check your terminal; close this tab.",
                )
            except PermissionError:
                outcome["error"] = True
                message = "Google consent was not completed. Check your terminal."
            except (LoginError, ValueError):
                pass
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
            self.end_headers()
            self.wfile.write(message.encode())

    return Handler


class CallbackServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


def login(path, calendar=False):
    private_path(path)  # Fail before consent if we cannot protect the resulting JWT.
    previous = read_session(path) if calendar else None
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    body = {"redirect_uri": CALLBACK, "code_challenge": challenge.decode()}
    if calendar:
        body["capabilities"] = ["calendar_read"]
    outcome = {}
    # Bind before starting consent; don't send the user to a different local process.
    with CallbackServer(("127.0.0.1", 8765), callback_handler("", outcome)) as server:
        start = api_request(
            "/auth/google/reconnect" if calendar else "/auth/google/begin",
            body,
            previous["jwt"] if previous else None,
        )
        state, url = validate_start(start, challenge.decode())
        server.RequestHandlerClass = callback_handler(state, outcome)
        server.timeout = 1
        deadline = time.monotonic() + 540
        print("Open this Google consent URL locally (do not share it):\n" + url, flush=True)
        webbrowser.open(url)
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    if outcome.get("error"):
        raise LoginError("Google consent was not completed. Restart login to retry.")
    if "code" not in outcome:
        raise LoginError("Login timed out. Restart login to retry.")
    session = validate_session(
        api_request(
            "/auth/google/exchange",
            {
                "code": outcome["code"],
                "state": state,
                "redirect_uri": CALLBACK,
                "code_verifier": verifier,
            },
        )
    )
    if previous and session["user"]["id"] != previous["user"]["id"]:
        raise LoginError("Reconnect returned a different account; session not overwritten.")
    save_session(path, session)
    print("GOOGLE_LOGIN_OK — session saved privately at", path)
    caps = api_request("/assistant/capabilities", jwt=session["jwt"])
    for capability in caps.get("capabilities", []):
        if capability.get("id") in {"gmail_read", "calendar_read", "gmail_send", "calendar_write"}:
            print(capability["id"], "ready=" + str(capability.get("ready") is True))
    print("No mailbox contents fetched, model invoked, email sent or event created by this helper.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--calendar", action="store_true", help="Reconnect the saved account for Calendar reads"
    )
    args = parser.parse_args()
    try:
        login(SESSION, args.calendar)
    except (LoginError, OSError) as exc:
        # Never print provider responses, tokens, authorization codes or private file contents.
        message = str(exc) if isinstance(exc, LoginError) else "Local callback/file unavailable."
        raise SystemExit(message) from None
    except KeyboardInterrupt:
        raise SystemExit("Login cancelled.") from None


if __name__ == "__main__":
    main()
