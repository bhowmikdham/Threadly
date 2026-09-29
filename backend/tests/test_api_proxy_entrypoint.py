"""Public ingress must never trust arbitrary browser-supplied proxy headers."""

import pytest
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.operations.api_entrypoint import uvicorn_command


def test_private_api_ignores_forwarded_headers_by_default():
    assert uvicorn_command()[-1] == "--no-proxy-headers"


def test_public_api_trusts_only_explicit_private_proxy_address():
    assert uvicorn_command("172.30.247.2")[-3:] == [
        "--proxy-headers",
        "--forwarded-allow-ips",
        "172.30.247.2",
    ]
    for untrusted in ("*", "0.0.0.0", "127.0.0.1", "203.0.113.1", "192.168.254.2,10.0.0.1"):
        with pytest.raises(ValueError, match="one private IP address"):
            uvicorn_command(untrusted)


@pytest.mark.asyncio
async def test_only_pinned_proxy_can_set_oauth_peer_address():
    async def app(scope, _receive, _send):
        seen.append(scope["client"][0])

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        pass

    seen = []
    middleware = ProxyHeadersMiddleware(app, trusted_hosts=["172.30.247.2"])
    for peer in ("172.30.247.2", "198.51.100.30"):
        await middleware(
            {
                "type": "http",
                "scheme": "http",
                "client": (peer, 12345),
                "headers": [(b"x-forwarded-for", b"203.0.113.25")],
            },
            receive,
            send,
        )
    assert seen == ["203.0.113.25", "198.51.100.30"]
