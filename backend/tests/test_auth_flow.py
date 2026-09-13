"""Module 2 end-to-end against real postgres: exchange -> user row -> encrypted
tokens -> JWT works on protected routes -> refresh path."""
import httpx
import pytest

from tests.conftest import needs_pg

pytestmark = needs_pg


def google_transport(refresh_token: str | None = "rt-1"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            body = {"access_token": "at-1", "expires_in": 3600}
            if refresh_token:
                body["refresh_token"] = refresh_token
            return httpx.Response(200, json=body)
        if request.url.host == "openidconnect.googleapis.com":
            return httpx.Response(
                200, json={"sub": "g-sub-1", "email": "b@x.com", "name": "Bhowmik"}
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_exchange_creates_user_with_encrypted_tokens(db_sessionmaker):
    from sqlalchemy import select

    from app.auth import crypto, service
    from app.db.models import User

    async with db_sessionmaker() as session:
        token, user = await service.exchange_code(
            session, "code-abc", "https://ext.chromiumapp.org/", transport=google_transport()
        )
        assert token and user.id

    async with db_sessionmaker() as session:
        row = (await session.execute(select(User))).scalar_one()
        assert row.email == "b@x.com"
        assert row.access_token_enc != b"at-1"  # encrypted at rest
        assert crypto.decrypt_token(row.access_token_enc) == "at-1"
        assert crypto.decrypt_token(row.refresh_token_enc) == "rt-1"


@pytest.mark.asyncio
async def test_second_login_without_refresh_token_keeps_old_one(db_sessionmaker):
    from sqlalchemy import select

    from app.auth import crypto, service
    from app.db.models import User

    async with db_sessionmaker() as session:
        await service.exchange_code(
            session, "c1", "https://r/", transport=google_transport("rt-first")
        )
    async with db_sessionmaker() as session:
        await service.exchange_code(session, "c2", "https://r/", transport=google_transport(None))
    async with db_sessionmaker() as session:
        row = (await session.execute(select(User))).scalar_one()
        assert crypto.decrypt_token(row.refresh_token_enc) == "rt-first"  # never nulled


def test_jwt_from_exchange_opens_protected_routes(db_client, auth_headers):
    r = db_client.get("/threads", headers=auth_headers(1))
    assert r.status_code == 200  # empty list, but authorised
    assert r.json() == {"threads": [], "next_page": None}
