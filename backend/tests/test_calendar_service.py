"""Route/service/PostgreSQL ownership, grant and race tests with fake Google transport."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select, text

from app.api.errors import ApiError
from app.auth import crypto, flow
from app.auth import service as auth_service
from app.auth.google import CALENDAR_SCOPES
from app.calendar import client, service
from app.db.models import CalendarEvidence, CalendarPreference, User
from app.schemas.calendar import FreeBusyRequest, SavePreferences
from tests.conftest import needs_pg
from tests.test_calendar_client import preferences

pytestmark = needs_pg


@pytest.fixture()
def setup(db_sessionmaker, monkeypatch):
    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    monkeypatch.setattr(auth_service, "get_session_factory", lambda: db_sessionmaker)

    async def seed():
        async with db_sessionmaker() as session:
            for uid in (1, 2):
                session.add(
                    User(
                        id=uid,
                        google_sub=f"calendar-{uid}",
                        email=f"owner{uid}@example.test",
                        google_connected=True,
                        google_email_verified=True,
                        google_scopes=CALENDAR_SCOPES,
                        access_token_enc=crypto.encrypt_token(f"fixture-{uid}"),
                        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
            await session.commit()

    asyncio.run(seed())
    state = SimpleNamespace(calls=[], missing=False, unknown=False, callback=None, check_idle=True)

    async def assert_no_transactions():
        # Use only at a quiescent point: another concurrent request may legitimately
        # still be in its pre-network snapshot transaction.
        async with db_sessionmaker() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE "
                        "datname=current_database() AND state='idle in transaction'"
                    )
                )
                == 0
            )

    state.assert_no_transactions = assert_no_transactions

    async def handler(req):
        state.calls.append(req)
        if state.check_idle:
            await assert_no_transactions()
        if state.callback:
            await state.callback(req)
        if req.url.path.endswith("calendarList"):
            cid = (
                "work@example.test"
                if req.headers["authorization"] == "Bearer fixture-1"
                else "other"
            )
            return httpx.Response(
                200,
                json={
                    "items": []
                    if state.missing
                    else [{"id": cid, "summary": "Private title", "accessRole": "reader"}]
                },
            )
        query = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "timeMin": query["timeMin"],
                "timeMax": query["timeMax"],
                "calendars": {
                    item["id"]: {"errors": [{"reason": "secret-provider-detail"}]}
                    if state.unknown
                    else {"busy": []}
                    for item in query["items"]
                },
            },
        )

    original = client._request

    async def request(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return await original(*args, **kwargs)

    monkeypatch.setattr(client, "_request", request)
    return state


def save_body(version=0, **changes):
    return {"expected_version": version, "preferences": preferences(**changes)}


def window(version=1):
    start = datetime.now(UTC) + timedelta(days=1)
    return {
        "expected_preferences_version": version,
        "start": start.isoformat(),
        "end": (start + timedelta(hours=8)).isoformat(),
    }


def test_routes_roundtrip_owner_grants_and_evidence(db_client, auth_headers, setup):
    h = auth_headers(1)
    assert db_client.get("/calendar/calendars").status_code == 401
    assert db_client.get("/calendar/preferences", headers=h).status_code == 404
    response = db_client.get("/calendar/calendars", headers=h)
    assert (
        response.status_code == 200 and response.json()["calendars"][0]["event_write_acl"] is False
    )
    assert response.headers["cache-control"] == "no-store"
    saved = db_client.put("/calendar/preferences", headers=h, json=save_body())
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert db_client.get("/calendar/preferences", headers=auth_headers(2)).status_code == 404
    assert db_client.put("/calendar/preferences", headers=h, json=save_body()).status_code == 409
    assert (
        db_client.put(
            "/calendar/preferences", headers=auth_headers(2), json=save_body()
        ).status_code
        == 422
    )
    result = db_client.post("/calendar/freebusy", headers=h, json=window())
    assert result.status_code == 201, result.text
    evidence = result.json()
    assert evidence["coverage"] == "complete" and evidence["calendars"][0]["busy"] == []
    route = "/calendar/freebusy/" + evidence["id"]
    assert db_client.get(route, headers=h).status_code == 200
    assert db_client.get(route, headers=auth_headers(2)).status_code == 404
    assert (
        db_client.put(
            "/calendar/preferences", headers=h, json=save_body(1, default_duration_minutes=45)
        ).status_code
        == 200
    )
    assert db_client.get(route, headers=h).status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        {"google_scopes": None},
        {"google_scopes": [CALENDAR_SCOPES[0]]},
        {"google_scopes": [CALENDAR_SCOPES[1]]},
        {"google_connected": False},
        {"google_email_verified": False},
        {"access_token_enc": None},
    ],
)
def test_partial_revoked_and_missing_grants_fail_before_network(
    db_client, db_sessionmaker, auth_headers, setup, change
):
    async def update():
        async with db_sessionmaker() as session:
            user = await session.get(User, 1)
            for key, value in change.items():
                setattr(user, key, value)
            await session.commit()

    asyncio.run(update())
    assert db_client.get("/calendar/calendars", headers=auth_headers(1)).status_code == 403
    assert setup.calls == []


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_error_calendar_is_unknown(db_client, auth_headers, setup, missing):
    h = auth_headers(1)
    assert db_client.put("/calendar/preferences", headers=h, json=save_body()).status_code == 200
    setup.missing, setup.unknown = missing, not missing
    response = db_client.post("/calendar/freebusy", headers=h, json=window())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["coverage"] == "unknown" and body["calendars"][0]["status"] == "unknown"
    assert body["calendars"][0]["reason"] == ("not_accessible" if missing else "provider_error")
    assert "secret-provider-detail" not in response.text


def test_concurrent_creation_and_updates_conflict(db_sessionmaker, setup):
    setup.check_idle = False

    async def race(expected):
        count, arrived = 0, asyncio.Event()

        async def barrier(req):
            nonlocal count
            count += 1
            if count == 2:
                # Both calls are now inside mocked HTTP and the first is blocked.
                await setup.assert_no_transactions()
                arrived.set()
            await asyncio.wait_for(arrived.wait(), 5)

        setup.callback = barrier
        results = await asyncio.gather(
            *[
                service.save_preferences(1, SavePreferences.model_validate(save_body(expected)))
                for _ in range(2)
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(item, ApiError) and item.status == 409 for item in results) == 1, (
            results
        )
        async with db_sessionmaker() as session:
            row = await session.get(CalendarPreference, 1)
            assert row.version == expected + 1

    asyncio.run(race(0))
    asyncio.run(race(1))


@pytest.mark.parametrize("change", ["preferences", "disconnect", "account"])
def test_changes_while_provider_waits_discard_evidence(db_sessionmaker, setup, change):
    async def run():
        await service.save_preferences(1, SavePreferences.model_validate(save_body()))

        async def update(req):
            if not req.url.path.endswith("freeBusy"):
                return
            async with db_sessionmaker() as session:
                if change == "preferences":
                    row = await session.get(CalendarPreference, 1, with_for_update=True)
                    row.version += 1
                else:
                    row = await session.get(User, 1, with_for_update=True)
                    row.google_account_version += 1
                    if change == "disconnect":
                        row.google_connected = False
                await session.commit()

        setup.callback = update
        with pytest.raises(ApiError) as error:
            await service.query_freebusy(1, FreeBusyRequest.model_validate(window()))
        assert error.value.status in (403, 409)
        async with db_sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(CalendarEvidence)) == 0

    asyncio.run(run())


def test_failed_persistence_rolls_back_without_evidence(db_sessionmaker, setup, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    async def run():
        await service.save_preferences(1, SavePreferences.model_validate(save_body()))
        original = AsyncSession.commit

        async def fail(session):
            if any(isinstance(row, CalendarEvidence) for row in session.new):
                raise RuntimeError("synthetic storage failure")
            return await original(session)

        monkeypatch.setattr(AsyncSession, "commit", fail)
        with pytest.raises(RuntimeError):
            await service.query_freebusy(1, FreeBusyRequest.model_validate(window()))
        async with db_sessionmaker() as session:
            assert await session.scalar(select(func.count()).select_from(CalendarEvidence)) == 0

    asyncio.run(run())


def test_expired_evidence_and_horizon(db_client, db_sessionmaker, auth_headers, setup):
    h = auth_headers(1)
    assert db_client.put("/calendar/preferences", headers=h, json=save_body()).status_code == 200
    response = db_client.post("/calendar/freebusy", headers=h, json=window())
    assert response.status_code == 201
    eid = response.json()["id"]

    async def expire():
        # ORM test database has no immutable migration trigger; migration tests cover that guard.
        async with db_sessionmaker() as session:
            row = await session.get(CalendarEvidence, eid)
            row.checked_at -= timedelta(days=1)
            row.expires_at -= timedelta(days=1)
            await session.commit()

    asyncio.run(expire())
    assert db_client.get("/calendar/freebusy/" + eid, headers=h).status_code == 409
    data = window()
    for key in ("start", "end"):
        data[key] = (datetime.fromisoformat(data[key]) + timedelta(days=100)).isoformat()
    before = len(setup.calls)
    assert db_client.post("/calendar/freebusy", headers=h, json=data).status_code == 422
    assert len(setup.calls) == before


def test_calendar_incremental_consent_only_on_authenticated_reconnect(
    db_client, auth_headers, setup, monkeypatch
):
    monkeypatch.setattr(
        flow,
        "get_settings",
        lambda: SimpleNamespace(
            google_client_id="client",
            google_client_secret="fixture",
            google_redirect_uri_allowlist_values={"https://ext.chromiumapp.org/"},
        ),
    )
    body = {"redirect_uri": "https://ext.chromiumapp.org/", "code_challenge": "a" * 43}
    result = db_client.post("/auth/google/begin", json=body)
    assert result.status_code == 200
    scopes = parse_qs(urlsplit(result.json()["authorization_url"]).query)["scope"][0].split()
    assert not set(scopes).intersection(CALENDAR_SCOPES)
    body["capabilities"] = ["calendar_read"]
    assert db_client.post("/auth/google/begin", json=body).status_code == 422
    result = db_client.post("/auth/google/reconnect", json=body, headers=auth_headers(1))
    assert result.status_code == 200, result.text
    scopes = parse_qs(urlsplit(result.json()["authorization_url"]).query)["scope"][0].split()
    assert set(CALENDAR_SCOPES) <= set(scopes)
    assert "https://www.googleapis.com/auth/calendar.events" not in scopes
    body["capabilities"] = ["calendar_write"]
    assert (
        db_client.post("/auth/google/reconnect", json=body, headers=auth_headers(1)).status_code
        == 422
    )
    capability = db_client.get("/assistant/capabilities", headers=auth_headers(1)).json()
    by_id = {item["id"]: item for item in capability["capabilities"]}
    assert by_id["calendar_read"]["ready"] and by_id["calendar_list"]["ready"]
    assert not by_id["calendar_write"]["ready"]


@pytest.mark.parametrize("operation", ["list", "save"])
def test_connection_changes_during_listing_are_fenced(db_sessionmaker, setup, operation):
    async def run():
        async def change(req):
            async with db_sessionmaker() as session:
                user = await session.get(User, 1, with_for_update=True)
                user.google_account_version += 1
                await session.commit()

        setup.callback = change
        with pytest.raises(ApiError) as error:
            if operation == "list":
                await service.list_calendars(1)
            else:
                await service.save_preferences(1, SavePreferences.model_validate(save_body()))
        assert error.value.code == "calendar_context_changed"
        async with db_sessionmaker() as session:
            assert await session.get(CalendarPreference, 1) is None

    asyncio.run(run())


def test_token_refresh_grant_change_stops_before_calendar_network(
    db_sessionmaker, setup, monkeypatch
):
    async def refresh(_session, user):
        async with db_sessionmaker() as session:
            current = await session.get(User, user.id)
            current.google_account_version += 1
            await session.commit()
        return "renewed-fixture-token"

    monkeypatch.setattr(service, "get_valid_access_token", refresh)
    with pytest.raises(ApiError) as error:
        asyncio.run(service.list_calendars(1))
    assert error.value.code == "calendar_context_changed" and setup.calls == []
