"""R18: every non-2xx body is the envelope — these lock the shape early."""


def _assert_envelope(body: dict):
    assert set(body.keys()) == {"error"}
    assert {"code", "message", "detail"} <= set(body["error"].keys())


def test_missing_token_is_enveloped_401(client):
    r = client.get("/threads")
    assert r.status_code == 401
    _assert_envelope(r.json())
    assert r.json()["error"]["code"] == "unauthorized"


def test_unknown_route_is_enveloped_404(client):
    r = client.get("/definitely-not-a-route")
    assert r.status_code == 404
    _assert_envelope(r.json())


def test_stub_endpoints_are_enveloped_501_when_authed(client):
    import jwt

    from app.config import get_settings

    settings = get_settings()
    token = jwt.encode({"sub": "1"}, settings.secret_key, algorithm=settings.jwt_algorithm)
    r = client.get("/threads", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 501
    _assert_envelope(r.json())
    assert r.json()["error"]["code"] == "not_implemented"
