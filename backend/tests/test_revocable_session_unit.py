"""Fast checks for generation-bound bearer validation without a database server."""

from types import SimpleNamespace

import jwt
import pytest

from app.api.deps import get_current_session
from app.api.errors import ApiError
from app.auth.service import issue_session_jwt, logout_threadly_session, refresh_session_jwt
from app.config import get_settings


class Result:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class Session:
    def __init__(self, row):
        self.row = row
        self.selected = False
        self.locked = False
        self.rolled_back = False
        self.committed = False

    async def execute(self, statement):
        self.selected = True
        return Result(self.row)

    async def get(self, model, user_id, *, with_for_update, populate_existing):
        self.locked = with_for_update and populate_existing
        return self.row

    async def rollback(self):
        self.rolled_back = True

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_live_generations_are_required_for_every_bearer():
    settings = get_settings()
    good = issue_session_jwt(4, 7, 3)
    live = Session(
        SimpleNamespace(google_account_version=7, threadly_session_version=3, google_connected=True)
    )
    identity = await get_current_session(live, authorization="Bearer " + good, settings=settings)
    assert (identity.user_id, identity.account_version, identity.session_version) == (4, 7, 3)
    assert live.selected

    for row in (
        None,
        SimpleNamespace(
            google_account_version=8, threadly_session_version=3, google_connected=True
        ),
        SimpleNamespace(
            google_account_version=7, threadly_session_version=4, google_connected=True
        ),
        SimpleNamespace(
            google_account_version=7, threadly_session_version=3, google_connected=False
        ),
    ):
        with pytest.raises(ApiError) as error:
            await get_current_session(
                Session(row), authorization="Bearer " + good, settings=settings
            )
        assert error.value.status == 401 and error.value.code == "reauth_required"

    for payload in (
        {"sub": "4"},  # old release had no account generation
        {"sub": "4", "av": "7"},
        {"sub": "4", "av": True},
        {"sub": "4", "av": 7},  # interim bearer with no logout generation
        {"sub": "4", "av": 7, "sv": "3"},
    ):
        legacy = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
        session = Session(
            SimpleNamespace(
                google_account_version=7, threadly_session_version=3, google_connected=True
            )
        )
        with pytest.raises(ApiError) as error:
            await get_current_session(session, authorization="Bearer " + legacy, settings=settings)
        assert error.value.code == "reauth_required"
        assert not session.selected


@pytest.mark.asyncio
async def test_refresh_checks_locked_versions_and_releases_lock():
    row = SimpleNamespace(
        id=4, google_account_version=7, threadly_session_version=3, google_connected=True
    )
    session = Session(row)
    token = await refresh_session_jwt(
        session, user_id=4, expected_account_version=7, expected_session_version=3
    )
    assert session.locked and session.rolled_back
    claims = jwt.decode(token, get_settings().secret_key, algorithms=[get_settings().jwt_algorithm])
    assert (claims["av"], claims["sv"]) == (7, 3)

    for stale in (
        None,
        SimpleNamespace(
            id=4, google_account_version=8, threadly_session_version=3, google_connected=True
        ),
        SimpleNamespace(
            id=4, google_account_version=7, threadly_session_version=4, google_connected=True
        ),
        SimpleNamespace(
            id=4, google_account_version=7, threadly_session_version=3, google_connected=False
        ),
    ):
        with pytest.raises(ApiError) as error:
            await refresh_session_jwt(
                Session(stale), user_id=4, expected_account_version=7, expected_session_version=3
            )
        assert error.value.code == "reauth_required"


@pytest.mark.asyncio
async def test_logout_advances_only_threadly_generation():
    row = SimpleNamespace(
        id=4, google_account_version=7, threadly_session_version=3, google_connected=True
    )
    session = Session(row)
    await logout_threadly_session(
        session, user_id=4, expected_account_version=7, expected_session_version=3
    )
    assert session.locked and session.committed
    assert row.threadly_session_version == 4
    assert row.google_account_version == 7 and row.google_connected
    with pytest.raises(ApiError) as error:
        await logout_threadly_session(
            Session(row), user_id=4, expected_account_version=7, expected_session_version=3
        )
    assert error.value.code == "reauth_required"
