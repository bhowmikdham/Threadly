"""Approved single-event lifecycle, real DB and mock Google. Never live invitations."""

# ruff: noqa: F811
import copy
import json

import httpx
import pytest
from sqlalchemy import func, select, update

from app.actions import calendar_executor as executor
from app.actions import calendar_preview as preview
from app.actions import calendar_worker
from app.actions import worker as email_worker
from app.assistant import worker
from app.config import get_settings
from app.db.models import ActionAttempt, AssistantAction, Thread, User
from app.schemas.actions import ApproveActionRequest
from tests.conftest import needs_pg
from tests.test_assistant_scheduling import capture, setup  # noqa: F401
from tests.test_calendar_service import setup as calendar_setup  # noqa: F401
from tests.test_mvp_workflows import body

pytestmark = needs_pg


async def proposed(db_sessionmaker, db_client, auth_headers):
    async with db_sessionmaker.begin() as session:
        user = await session.get(User, 1)
        user.google_scopes = user.google_scopes + [
            "https://www.googleapis.com/auth/calendar.events"
        ]
    req = body(
        capture(db_client, auth_headers),
        operations=["schedule"],
        draft_options=None,
        summary_in_draft=False,
    )
    response = db_client.post("/assistant/workflow-requests", headers=auth_headers(1), json=req)
    assert response.status_code == 202, response.text
    await worker.run_once(db_sessionmaker)
    result = db_client.get(
        "/assistant/tasks/" + response.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert result["state"] == "succeeded", result
    artifact_id = result["artifact_id"]
    artifact = db_client.get("/assistant/artifacts/" + artifact_id, headers=auth_headers(1)).json()
    query_id = artifact["artifact"]["content"]["slot_request_id"]
    neg = db_client.post(
        "/calendar/negotiations",
        headers=auth_headers(1),
        json={"request_id": "neg", "thread_id": "schedule-thread", "expected_thread_version": 1},
    )
    assert neg.status_code == 201, neg.text
    path = "/calendar/negotiations/" + neg.json()["id"]
    offer = db_client.post(
        path + "/offers",
        headers=auth_headers(1),
        json={
            "request_id": "offer",
            "expected_version": 1,
            "expected_thread_version": 1,
            "slot_request_id": query_id,
        },
    )
    assert offer.status_code == 201, offer.text
    offer = offer.json()
    selection = db_client.post(
        path + "/selections",
        headers=auth_headers(1),
        json={
            "request_id": "select",
            "expected_version": offer["current_version"],
            "offer_id": offer["id"],
            "slot_id": offer["slots"][0]["id"],
        },
    )
    assert selection.status_code == 202, selection.text
    request = {
        "request_id": "book-preview",
        "expected_revision": 1,
        "selection_id": selection.json()["id"],
        "calendar_id": "work@example.test",
        "title": "Project check-in",
        "attendees": ["guest@example.test"],
        "send_updates": "all",
    }
    url = f"/assistant/artifacts/{artifact_id}/calendar-actions"
    result = db_client.post(url, headers=auth_headers(1), json=request)
    assert result.status_code == 201, result.text
    assert (
        db_client.post(url, headers=auth_headers(1), json=request).json()["action_id"]
        == result.json()["action_id"]
    )
    return result.json()


class Google:
    def __init__(self, mode="success"):
        self.mode, self.calls, self.event = mode, [], None
        self.transport = httpx.MockTransport(self.handle)

    async def handle(self, req):
        self.calls.append(req)
        if req.method == "POST":
            self.event = json.loads(req.content)
            if self.mode == "timeout":
                raise httpx.ReadTimeout("lost response")
            return httpx.Response(200, json=self.event)
        if self.mode == "missing":
            return httpx.Response(404, json={"error": {"code": 404}})
        event = copy.deepcopy(self.event)
        if self.mode == "collision":
            event["extendedProperties"]["private"]["threadlyAction"] = "different"
        return httpx.Response(200, json=event)


async def approve(db_sessionmaker, action, google):
    async with db_sessionmaker.begin() as session:
        await preview.approve(
            session,
            1,
            action["action_id"],
            ApproveActionRequest(
                request_id="approve",
                expected_version=action["version"],
                payload_hash=action["payload_hash"],
            ),
            transport=google.transport,
        )


async def allow_acl(*args, **kwargs):
    return [{"id": "work@example.test", "event_write_acl": True}]


async def token(owner):
    return "fixture-1"


@pytest.mark.parametrize("mode", ["success", "timeout"])
async def test_booking_success_or_lost_response_reconciles(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch, mode
):
    action = await proposed(db_sessionmaker, db_client, auth_headers)
    assert not action["approval_available"]
    google = Google(mode)
    monkeypatch.setattr(get_settings(), "calendar_writes_enabled", True)
    monkeypatch.setattr(get_settings(), "calendar_reconciliation_enabled", True)
    # Existing read service fake verifies no open DB transaction during network I/O.
    monkeypatch.setattr(executor.client, "list_calendars", allow_acl)
    await approve(db_sessionmaker, action, google)
    async with db_sessionmaker.begin() as session:
        assert await email_worker.candidate(session) is None
    assert await calendar_worker.run_once(
        db_sessionmaker, transport=google.transport, token_loader=token
    )
    async with db_sessionmaker() as session:
        row = await session.get(AssistantAction, action["action_id"])
        assert row.state == ("succeeded" if mode == "success" else "outcome_unknown"), (
            row.error_code
        )
    if mode == "timeout":
        assert await calendar_worker.run_once(
            db_sessionmaker, transport=google.transport, token_loader=token
        )
    async with db_sessionmaker() as session:
        row = await session.get(AssistantAction, action["action_id"])
        assert row.state == "succeeded", row.error_code
        assert row.result["event_id"] == action["preview"]["event"]["id"]
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 1
    assert sum(req.method == "POST" for req in google.calls) == 1
    assert not await calendar_worker.run_once(
        db_sessionmaker, transport=google.transport, token_loader=token
    )


async def test_changed_context_blocks_approved_booking(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch
):
    action = await proposed(db_sessionmaker, db_client, auth_headers)
    google = Google()
    monkeypatch.setattr(get_settings(), "calendar_writes_enabled", True)
    await approve(db_sessionmaker, action, google)
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Thread).values(version=2))
    assert await calendar_worker.run_once(
        db_sessionmaker, transport=google.transport, token_loader=token
    )
    assert not google.calls
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantAction, action["action_id"])).state == "superseded"
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 0


async def test_busy_after_approval_blocks_insert(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch
):
    action = await proposed(db_sessionmaker, db_client, auth_headers)
    google = Google()
    monkeypatch.setattr(get_settings(), "calendar_writes_enabled", True)
    await approve(db_sessionmaker, action, google)

    async def busy(*args, **kwargs):
        return "calendar_busy_after_approval"

    monkeypatch.setattr(executor, "preflight", busy)
    assert await calendar_worker.run_once(
        db_sessionmaker, transport=google.transport, token_loader=token
    )
    assert not google.calls
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantAction, action["action_id"])).state == "superseded"


async def test_event_reconciliation_wrong_marker_never_reinserts(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch
):
    action = await proposed(db_sessionmaker, db_client, auth_headers)
    google = Google("timeout")
    monkeypatch.setattr(get_settings(), "calendar_writes_enabled", True)
    monkeypatch.setattr(get_settings(), "calendar_reconciliation_enabled", True)
    monkeypatch.setattr(executor.client, "list_calendars", allow_acl)
    await approve(db_sessionmaker, action, google)
    await calendar_worker.run_once(db_sessionmaker, transport=google.transport, token_loader=token)
    google.mode = "collision"
    await calendar_worker.run_once(db_sessionmaker, transport=google.transport, token_loader=token)
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantAction, action["action_id"])).state == "outcome_unknown"
    assert sum(req.method == "POST" for req in google.calls) == 1


async def test_crash_after_dispatch_is_reconciled_without_second_insert(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch
):
    from app.db.models import ActionJob

    action = await proposed(db_sessionmaker, db_client, auth_headers)
    google = Google()
    monkeypatch.setattr(get_settings(), "calendar_writes_enabled", True)
    monkeypatch.setattr(get_settings(), "calendar_reconciliation_enabled", True)
    await approve(db_sessionmaker, action, google)
    claim = await calendar_worker.claim_one(db_sessionmaker, transport=google.transport)
    assert await calendar_worker.claim_one(db_sessionmaker, transport=google.transport) is None
    dispatch = await calendar_worker.prepare(
        db_sessionmaker, claim, transport=google.transport, dispatch=True
    )
    result = await executor.request_event("fixture", dispatch.request, transport=google.transport)
    assert result.state == "succeeded"
    # Lose the response/worker before it can record success.
    async with db_sessionmaker.begin() as session:
        row = await session.get(ActionJob, action["action_id"])
        row.lease_expires_at = await session.scalar(select(func.clock_timestamp()))
    assert await email_worker.recover_one(db_sessionmaker, action_type="create_event")
    assert await calendar_worker.reconcile_one(
        db_sessionmaker, transport=google.transport, token_loader=token
    )
    assert [r.method for r in google.calls] == ["POST", "GET"]
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantAction, action["action_id"])).state == "succeeded"


async def test_api_pilot_gate_and_foreign_approval(
    db_sessionmaker, setup, db_client, auth_headers, monkeypatch
):
    action = await proposed(db_sessionmaker, db_client, auth_headers)
    monkeypatch.setattr(get_settings(), "calendar_writes_enabled", True)
    url = "/assistant/calendar-actions/" + action["action_id"] + "/approve"
    request = {
        "request_id": "pilot",
        "expected_version": action["version"],
        "payload_hash": action["payload_hash"],
    }
    assert db_client.post(url, headers=auth_headers(2), json=request).status_code == 404
    assert db_client.post(url, headers=auth_headers(1), json=request).status_code == 409
    monkeypatch.setattr(get_settings(), "write_pilot_user_ids", "1")
    response = db_client.post(url, headers=auth_headers(1), json=request)
    assert response.status_code == 202, response.text
    # Approval queues work but does not itself call Google.
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(ActionAttempt)) == 0
