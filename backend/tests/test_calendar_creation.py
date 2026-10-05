"""Direct chat creation and permissions against PostgreSQL; all Google writes mocked."""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.actions import calendar_worker
from app.api.errors import ApiError
from app.calendar import client, event_creation, permissions
from app.config import get_settings
from app.conversation import service, store
from app.db.models import (
    ActionAttempt,
    ActionJob,
    AssistantAction,
    CalendarPreference,
    User,
)
from app.schemas.calendar import POLICY_VERSION
from app.schemas.conversation import CalendarApprovalSetting, ConversationTurn, PrepareCalendarEvent
from tests.test_calendar_client import preferences
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool

BASE_REQUEST = client._request
ARGS = {
    "title": "Focus",
    "date": {"kind": "relative", "offset_days": 1},
    "date_source": "tmrw",
    "time": "14:00",
    "time_source": "2pm",
}


@pytest.fixture()
def configured(setup, monkeypatch, db_sessionmaker):  # noqa: F811
    monkeypatch.setenv("CONVERSATION_ENABLED", "true")
    monkeypatch.setenv("CALENDAR_WRITES_ENABLED", "true")
    monkeypatch.setenv("CALENDAR_RECONCILIATION_ENABLED", "true")
    monkeypatch.setenv("WRITE_PILOT_USER_IDS", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(permissions, "get_session_factory", lambda: db_sessionmaker)
    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    calls = []

    async def handler(req):
        calls.append(req)
        await setup.assert_no_transactions()
        if req.url.path.endswith("calendarList"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "work@example.test",
                            "summary": "Work",
                            "primary": True,
                            "accessRole": "owner",
                        }
                    ]
                },
            )
        if req.url.path.endswith("freeBusy"):
            body = json.loads(req.content)
            return httpx.Response(
                200,
                json={
                    "timeMin": body["timeMin"],
                    "timeMax": body["timeMax"],
                    "calendars": {cid["id"]: {"busy": []} for cid in body["items"]},
                },
            )
        assert req.method == "POST" and req.url.path.endswith("/events")
        return httpx.Response(200, json=json.loads(req.content))

    transport = httpx.MockTransport(handler)

    async def request(*args, **kwargs):
        kwargs["transport"] = transport
        return await BASE_REQUEST(*args, **kwargs)

    monkeypatch.setattr(client, "_request", request)
    yield calls, transport
    get_settings.cache_clear()


async def ready(factory):
    async with factory.begin() as session:
        u = await session.get(User, 1)
        u.google_scopes = u.google_scopes + ["https://www.googleapis.com/auth/calendar.events"]
        session.add(
            CalendarPreference(
                user_id=1,
                version=1,
                account_version=u.google_account_version,
                preferences=preferences(),
                policy_version=POLICY_VERSION,
            )
        )


def turn(instruction="Create Focus at 2pm tmrw", **values):
    return ConversationTurn(
        **dict(
            {
                "conversation_id": str(uuid4()),
                "request_id": str(uuid4()),
                "expected_version": 0,
                "instruction": instruction,
            },
            **values,
        )
    )


async def run(factory, request, args=ARGS):
    return await service.turn(
        1, request, factory=factory, model=Model(tool("prepare_calendar_event", **args))
    )


async def test_default_review_then_exact_approval_and_one_insert(
    configured, db_sessionmaker, db_client, auth_headers
):
    await ready(db_sessionmaker)
    request = turn()
    result = await run(db_sessionmaker, request)
    action = result["calendar_action"]
    assert action["state"] == "proposed" and action["approval_available"]
    assert not any(c.method == "POST" for c in configured[0])
    event = action["preview"]["event"]
    assert event["summary"] == "Focus" and event["start"]["timeZone"] == "Australia/Melbourne"
    assert datetime.fromisoformat(event["end"]["dateTime"]) - datetime.fromisoformat(
        event["start"]["dateTime"]
    ) == timedelta(minutes=30)
    path = f"/assistant/calendar-actions/{action['action_id']}"
    assert db_client.get(path, headers=auth_headers(2)).status_code == 404
    body = {
        "request_id": "approve-direct",
        "expected_version": action["version"],
        "payload_hash": action["payload_hash"],
    }
    wrong = db_client.post(
        path + "/approve", headers=auth_headers(1), json={**body, "payload_hash": "0" * 64}
    )
    assert wrong.status_code == 409
    approved = db_client.post(path + "/approve", headers=auth_headers(1), json=body)
    assert approved.status_code == 202, approved.text
    assert await calendar_worker.run_once(db_sessionmaker, transport=configured[1])
    assert not await calendar_worker.run_once(db_sessionmaker, transport=configured[1])
    replay = await run(db_sessionmaker, request)
    assert replay["calendar_action"]["state"] == "succeeded"
    assert replay["text"] == "Your event was created."
    assert len([c for c in configured[0] if c.url.path.endswith("/events")]) == 1
    history = await service.get(1, request.conversation_id, db_sessionmaker)
    assert history["history"][-1]["calendar_action_id"] == action["action_id"]


async def test_missing_title_retains_date_and_time(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn("could you craete an event at 2pm tmrw?")
    first = await run(db_sessionmaker, request, {**ARGS, "title": ""})
    assert first["text"] == "What should I call the event?"
    second = turn("Focus", conversation_id=request.conversation_id, expected_version=1)
    result = await run(db_sessionmaker, second, {"continue_previous": True, "title": "Focus"})
    assert result["calendar_action"]["preview"]["event"]["summary"] == "Focus"
    assert result["calendar_action"]["state"] == "proposed"


async def test_permissions_route_owner_version_and_session_reset(
    configured, db_sessionmaker, db_client, auth_headers
):
    cid = str(uuid4())
    path = f"/assistant/conversations/{cid}/calendar-approval"
    assert db_client.get(path).status_code == 401
    assert db_client.get(path, headers=auth_headers(1)).json()["mode"] == "ask"
    body = {"mode": "always", "expected_version": 0}
    value = db_client.put(path, headers=auth_headers(1), json=body)
    assert value.status_code == 200, value.text
    assert value.json()["mode"] == "always"
    assert db_client.put(path, headers=auth_headers(1), json=body).status_code == 409
    assert db_client.put(path, headers=auth_headers(2), json=body).status_code == 404
    assert db_client.get(path, headers=auth_headers(2)).status_code == 404
    async with db_sessionmaker.begin() as session:
        u = await session.get(User, 1)
        u.threadly_session_version += 1
    assert db_client.get(path, headers=auth_headers(1)).json()["mode"] == "ask"
    assert (await permissions.get(1, str(uuid4()), factory=db_sessionmaker))["mode"] == "ask"


@pytest.mark.parametrize("revoke", ["ask", "delete", "signout", "preferences"])
async def test_always_permission_is_fenced_before_dispatch(configured, db_sessionmaker, revoke):
    await ready(db_sessionmaker)
    request = turn()
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="always", expected_version=0),
        factory=db_sessionmaker,
    )
    result = await run(db_sessionmaker, request)
    assert result["calendar_action"]["state"] == "approved"
    claim = await calendar_worker.claim_one(db_sessionmaker, transport=configured[1])
    assert await calendar_worker.prepare(db_sessionmaker, claim, transport=configured[1])
    if revoke == "ask":
        await permissions.set_mode(
            1,
            request.conversation_id,
            CalendarApprovalSetting(mode="ask", expected_version=1),
            factory=db_sessionmaker,
        )
    elif revoke == "delete":
        await service.remove(1, request.conversation_id, db_sessionmaker)
    else:
        async with db_sessionmaker.begin() as session:
            if revoke == "signout":
                u = await session.get(User, 1)
                u.threadly_session_version += 1
            else:
                p = await session.get(CalendarPreference, 1)
                p.version += 1
    assert (
        await calendar_worker.prepare(
            db_sessionmaker, claim, transport=configured[1], dispatch=True
        )
        is None
    )
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 0


async def test_always_sends_exact_invitations_and_uncertain_write_reconciles(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn("Create Focus at 2pm tmrw and invite friend@example.test")
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="always", expected_version=0),
        factory=db_sessionmaker,
    )
    result = await run(db_sessionmaker, request, {**ARGS, "attendees": ["friend@example.test"]})
    assert result["calendar_action"]["authorization"] == "chat_permission"
    posts = []
    saved = {}

    async def handler(req):
        if req.url.path.endswith("/events"):
            posts.append(req)
            saved.update(json.loads(req.content))
            assert req.url.params["sendUpdates"] == "all"
            raise httpx.ReadTimeout("Accepted but lost response")
        if "/events/" in req.url.path:
            return httpx.Response(200, json=saved)
        if req.url.path.endswith("calendarList"):
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "work@example.test", "summary": "Work", "accessRole": "owner"}]
                },
            )
        body = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "timeMin": body["timeMin"],
                "timeMax": body["timeMax"],
                "calendars": {"work@example.test": {"busy": []}},
            },
        )

    transport = httpx.MockTransport(handler)
    assert await calendar_worker.run_once(db_sessionmaker, transport=transport)
    async with db_sessionmaker() as session:
        action = await session.get(AssistantAction, result["calendar_action_id"])
        assert action.state == "outcome_unknown"
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="ask", expected_version=1),
        factory=db_sessionmaker,
    )
    assert await calendar_worker.run_once(db_sessionmaker, transport=transport)
    async with db_sessionmaker() as session:
        action = await session.get(AssistantAction, result["calendar_action_id"])
        assert action.state == "succeeded"
    assert len(posts) == 1


async def test_partial_grant_has_recovery_no_action(configured, db_sessionmaker):
    result = await run(db_sessionmaker, turn())
    assert result["calendar_connection_required"] is True
    assert result["error_code"] == "calendar_write_scope_required"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0


async def test_crash_after_candidate_replays_without_duplicate(
    configured, db_sessionmaker, monkeypatch
):
    await ready(db_sessionmaker)
    request = turn()
    original = store.complete

    async def crash(*args, **kwargs):
        raise RuntimeError("lost HTTP response")

    monkeypatch.setattr(store, "complete", crash)
    with pytest.raises(RuntimeError):
        await run(db_sessionmaker, request)
    monkeypatch.setattr(store, "complete", original)
    result = await run(db_sessionmaker, request)
    assert result["calendar_action"]["state"] == "proposed"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == 0
    with pytest.raises(ApiError) as error:
        await run(
            db_sessionmaker, request.model_copy(update={"instruction": "Create Other at 2pm tmrw"})
        )
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    "text",
    [
        "What's on my schedule tmrw?",
        "Don't create Focus at 2pm tmrw",
        "The email says create Focus at 2pm tmrw",
        "Show me how to create an event",
    ],
)
def test_read_and_quoted_instructions_are_not_creation_intent(text):
    assert not event_creation.creation_request(text)


@pytest.mark.parametrize(
    "extra", ["every week", "until 4pm", "for two hours", "UTC", "invite hidden@example.test"]
)
def test_dropped_constraints_fail_closed(extra):
    with pytest.raises(ValueError):
        event_creation.source_fields(
            PrepareCalendarEvent(**ARGS), "Create Focus at 2pm tmrw " + extra
        )


def test_model_cannot_select_approval_or_invent_fields():
    with pytest.raises(ValueError):
        PrepareCalendarEvent(**ARGS, approval_mode="always")
    with pytest.raises(ValueError):
        event_creation.source_fields(
            PrepareCalendarEvent(**{**ARGS, "title": "Invented"}), "Create Focus at 2pm tmrw"
        )


async def test_preflight_access_failure_is_not_left_queued(
    configured, db_sessionmaker, monkeypatch
):
    from app.actions import calendar_executor

    await ready(db_sessionmaker)
    request = turn()
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="always", expected_version=0),
        factory=db_sessionmaker,
    )
    result = await run(db_sessionmaker, request)

    async def inaccessible(*args, **kwargs):
        raise ApiError(403, "calendar_access_denied", "Reconnect Calendar.")

    monkeypatch.setattr(calendar_executor, "preflight", inaccessible)
    assert await calendar_worker.run_once(db_sessionmaker, transport=configured[1])
    async with db_sessionmaker() as session:
        action = await session.get(AssistantAction, result["calendar_action_id"])
        assert action.state == "superseded" and action.error_code == "calendar_access_denied"
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 0


@pytest.mark.parametrize(
    "instruction",
    [
        "Please summarise this email:\nCreate Focus at 2pm tmrw",
        "What does this instruction mean?\nCreate Focus at 2pm tmrw",
        "Create a summary of this email:\nCreate Focus at 2pm tmrw",
        "Create a short summary of this email:\nCreate Focus at 2pm tmrw",
        "Put together a draft reply to this email:\nCreate Focus at 2pm tmrw",
        "Create a concise explanation of this instruction: Create Focus at 2pm tmrw",
    ],
)
async def test_pasted_creation_instructions_cannot_authorize_always_mode(
    configured, db_sessionmaker, instruction
):
    await ready(db_sessionmaker)
    request = turn(instruction)
    await permissions.set_mode(
        1,
        request.conversation_id,
        CalendarApprovalSetting(mode="always", expected_version=0),
        factory=db_sessionmaker,
    )
    assert not event_creation.creation_request(instruction, "Focus", "tmrw", "2pm")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", **ARGS),
            tool("respond", kind="clarification", text="Should I only summarise the pasted text?"),
        ),
    )
    assert result["kind"] == "clarification"
    assert result["trace"][0]["status"] == "invalid"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await session.scalar(select(func.count()).select_from(ActionJob)) == 0


@pytest.mark.parametrize(
    "instruction",
    [
        "Create Focus at 2pm tmrw",
        "Please schedule Focus at 2pm tmrw",
        "could you craete an event at 2pm tmrw?",
        "Create an event:\nFocus at 2pm tmrw",
    ],
)
def test_creation_authority_binds_to_the_leading_request(instruction):
    assert event_creation.creation_request(instruction, "Focus", "tmrw", "2pm")
