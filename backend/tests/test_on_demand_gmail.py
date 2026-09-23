"""Real PostgreSQL / HTTP routes / worker; Google and model remain deterministic fakes."""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, text

from app.assistant import source_data, worker
from app.auth import crypto
from app.auth import service as auth_service
from app.config import get_settings
from app.db.models import ContextSnapshot, Message, Thread, User
from app.mail import dependency, live
from app.model_client.client import GenResult
from tests.conftest import needs_pg

pytestmark = needs_pg
TEXT = "Please review the revised agenda by Friday. PRIVATE-SOURCE-MARKER"
TID, MID = "abc123", "def456"


@pytest.fixture()
def setup(db_sessionmaker, monkeypatch):
    monkeypatch.setenv("GMAIL_SOURCE_MODE", "on_demand")
    get_settings.cache_clear()
    for module in (live, dependency, auth_service):
        monkeypatch.setattr(module, "get_session_factory", lambda: db_sessionmaker)

    async def seed():
        async with db_sessionmaker.begin() as session:
            for owner in (1, 2):
                session.add(
                    User(
                        id=owner,
                        google_sub=f"live-{owner}",
                        email=f"owner{owner}@example.test",
                        google_identity={
                            "sub": f"live-{owner}",
                            "email": f"owner{owner}@example.test",
                        },
                        google_connected=True,
                        google_email_verified=True,
                        google_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                        access_token_enc=crypto.encrypt_token(f"token-{owner}"),
                        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )

    asyncio.run(seed())
    state = SimpleNamespace(calls=[], text=TEXT, denied=False)

    async def handler(request):
        state.calls.append(request)
        async with db_sessionmaker() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND state='idle in transaction'"
                    )
                )
                == 0
            )
        if request.headers["authorization"] != "Bearer token-1" or state.denied:
            return httpx.Response(404, json={"error": "private"})
        message = {
            "id": MID,
            "threadId": TID,
            "labelIds": ["INBOX"],
            "internalDate": str(int(datetime.now(UTC).timestamp()) * 1000),
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "sender@example.test"},
                    {"name": "To", "value": "owner1@example.test"},
                    {"name": "Subject", "value": "Agenda"},
                    {"name": "Date", "value": "Mon, 21 Sep 2026 09:00:00 +1000"},
                    {"name": "Message-ID", "value": "<original@example.test>"},
                ],
                "body": {"data": base64.urlsafe_b64encode(state.text.encode()).decode()},
            },
        }
        # Fixed timestamps ensure fingerprint stability between independent reads.
        message["internalDate"] = "1790031600000"
        if request.url.path.endswith("/threads/" + TID):
            return httpx.Response(200, json={"id": TID, "messages": [message]})
        if request.url.path.endswith("/messages/" + MID):
            return httpx.Response(200, json=message)
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200, json={"messages": [{"id": MID, "threadId": TID}], "nextPageToken": "next-page"}
            )
        raise AssertionError("Unexpected Gmail read")

    original = live.GmailClient
    monkeypatch.setattr(
        live,
        "GmailClient",
        lambda token, transport=None: original(token, transport=httpx.MockTransport(handler)),
    )
    yield state
    get_settings.cache_clear()


def capture(client, headers):
    response = client.post(
        "/assistant/context-snapshots",
        headers=headers(1),
        json={"schema_version": "1.0", "thread_id": TID},
    )
    assert response.status_code == 201, response.text
    return response.json()


class Model:
    def __init__(self):
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        if "email draft" in prompt:
            result = {
                "subject": "Re: Agenda",
                "body": "Thanks for the agenda.",
                "unresolved_fields": [],
                "sources": [1],
            }
        else:
            result = {
                "overview": "The sender requests an agenda review by Friday.",
                "decisions": [],
                "actions": [],
                "open_questions": [],
            }
        return json.dumps(result), GenResult("fake", "on-demand-test")


async def test_reference_only_capture_and_worker_refetch(
    setup, db_client, auth_headers, db_sessionmaker
):
    snapshot = capture(db_client, auth_headers)
    assert TEXT in snapshot["messages"][0]["body"]
    async with db_sessionmaker() as session:
        stored = await session.get(ContextSnapshot, snapshot["context_snapshot_id"])
        assert stored.payload["storage"] == source_data.STORAGE
        assert "messages" not in stored.payload and TEXT not in json.dumps(stored.payload)
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        thread = await session.scalar(select(Thread))
        assert thread.subject is None
    response = db_client.post(
        "/assistant/requests",
        headers=auth_headers(1),
        json={
            "schema_version": "1.0",
            "request_id": "sum-1",
            "instruction": "Summarise this thread",
            "intent_hint": "summarise",
            "context_snapshot_id": snapshot["context_snapshot_id"],
            "continuation": None,
        },
    )
    assert response.status_code == 202, response.text
    before = len(setup.calls)
    model = Model()
    assert await worker.run_once(db_sessionmaker, model)
    assert len(setup.calls) > before  # Worker independently reloads Gmail; no process cache reuse.
    assert TEXT in model.calls[-1]
    task = db_client.get(
        "/assistant/tasks/" + response.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert task["state"] == "succeeded", task
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
        assert TEXT not in json.dumps(
            (await session.get(ContextSnapshot, snapshot["context_snapshot_id"])).payload
        )
    assert source_data._cache.get() is None


def test_foreign_context_and_changed_source_fail_closed(setup, db_client, auth_headers):
    snapshot = capture(db_client, auth_headers)
    calls = len(setup.calls)
    url = "/assistant/context-snapshots/" + snapshot["context_snapshot_id"]
    assert db_client.get(url, headers=auth_headers(2)).status_code == 404
    assert len(setup.calls) == calls
    setup.text = "A changed source"
    response = db_client.get(url, headers=auth_headers(1))
    assert response.status_code == 409 and response.json()["error"]["code"] == "source_changed"


async def test_deleted_source_stops_worker_before_model(
    setup, db_client, auth_headers, db_sessionmaker
):
    snapshot = capture(db_client, auth_headers)
    response = db_client.post(
        "/assistant/requests",
        headers=auth_headers(1),
        json={
            "schema_version": "1.0",
            "request_id": "sum-missing",
            "instruction": "Summarise this thread",
            "intent_hint": "summarise",
            "context_snapshot_id": snapshot["context_snapshot_id"],
            "continuation": None,
        },
    )
    assert response.status_code == 202
    setup.denied = True
    model = Model()
    await worker.run_once(db_sessionmaker, model)
    assert model.calls == []
    task = db_client.get(
        "/assistant/tasks/" + response.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert task["state"] == "failed" and task["error_code"] == "gmail_source_missing"


async def test_reply_without_stored_messages(setup, db_client, auth_headers, db_sessionmaker):
    snapshot = capture(db_client, auth_headers)
    response = db_client.post(
        "/assistant/requests",
        headers=auth_headers(1),
        json={
            "schema_version": "1.0",
            "request_id": "reply-1",
            "instruction": "Draft a reply",
            "intent_hint": "reply",
            "context_snapshot_id": snapshot["context_snapshot_id"],
            "continuation": None,
            "draft_options": {"reply_message_id": MID, "to": ["sender@example.test"]},
        },
    )
    assert response.status_code == 202, response.text
    model = Model()
    await worker.run_once(db_sessionmaker, model)
    task = db_client.get(
        "/assistant/tasks/" + response.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert task["state"] == "succeeded", task
    artifact = db_client.get("/assistant/artifacts/" + task["artifact_id"], headers=auth_headers(1))
    assert artifact.status_code == 200, artifact.text
    assert artifact.json()["draft_envelope"]["reply"]["rfc_message_id"] == "<original@example.test>"
    preview = db_client.post(
        "/assistant/artifacts/" + task["artifact_id"] + "/actions",
        headers=auth_headers(1),
        json={
            "request_id": "preview-live",
            "expected_revision": artifact.json()["revision"],
            "action_type": "send_email",
        },
    )
    assert preview.status_code == 201, preview.text
    action = preview.json()
    assert action["preview"]["in_reply_to"] == "<original@example.test>"
    setup.text = "Changed after preview"
    changed = db_client.get("/assistant/actions/" + action["action_id"], headers=auth_headers(1))
    assert changed.status_code == 409, changed.text
    setup.denied = True
    before = len(setup.calls)
    cancelled = db_client.post(
        "/assistant/actions/" + action["action_id"] + "/cancel",
        headers=auth_headers(1),
        json={"request_id": "cancel-offline", "expected_version": action["version"]},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["action"]["state"] == "cancelled"
    assert len(setup.calls) == before

    assert await _message_count(db_sessionmaker) == 0


async def _message_count(factory):
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(Message))


def test_sync_endpoints_retired_even_if_old_flag_true(setup, db_client, auth_headers):
    before = len(setup.calls)
    for path, body in (("/sync", None), ("/sync/jobs", {"request_id": "bad"})):
        result = db_client.post(path, headers=auth_headers(1), json=body)
        assert result.status_code == 410
    assert len(setup.calls) == before


def test_search_one_page_cursor_scope_no_persistence(setup, db_client, auth_headers):
    body = {
        "schema_version": "1.0",
        "query": "agenda",
        "folder": "INBOX",
        "received_from": "2026-09-01T00:00:00Z",
        "received_before": "2026-10-01T00:00:00Z",
    }
    result = db_client.post("/assistant/mail-search", headers=auth_headers(1), json=body)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["schema_version"] == "2.0" and len(data["results"]) == 1
    assert data["next_cursor"]
    lists = [r for r in setup.calls if r.url.path.endswith("/messages")]
    assert len(lists) == 1 and lists[0].url.params["maxResults"] == "20"
    assert "pageToken" not in lists[0].url.params
    result = db_client.post(
        "/assistant/mail-search",
        headers=auth_headers(1),
        json={**body, "query": "changed", "cursor": data["next_cursor"]},
    )
    assert result.status_code == 409
    assert len([r for r in setup.calls if r.url.path.endswith("/messages")]) == 1


@pytest.mark.parametrize("tid", ["../messages", "abc?alt=media", "https://attacker.test", "a/b"])
def test_invalid_source_ids_never_reach_google(setup, db_client, auth_headers, tid):
    result = db_client.post(
        "/assistant/context-snapshots",
        headers=auth_headers(1),
        json={"schema_version": "1.0", "thread_id": tid},
    )
    assert result.status_code == 422
    assert setup.calls == []


def test_thread_detail_no_db_mail(setup, db_client, auth_headers):
    result = db_client.get("/threads/" + TID, headers=auth_headers(1))
    assert result.status_code == 200, result.text
    assert result.json()["thread"]["version"] == 1
    assert result.json()["persisted_email_content"] is False
    assert result.headers["cache-control"] == "no-store"


async def test_summary_schedule_reply_graph_without_mailbox(
    setup, db_client, auth_headers, db_sessionmaker, monkeypatch
):
    from app.auth.google import CALENDAR_SCOPES
    from app.calendar import client as calendar_client
    from app.calendar import service, slots

    for module in (service, slots):
        monkeypatch.setattr(module, "get_session_factory", lambda: db_sessionmaker)
    async with db_sessionmaker.begin() as session:
        user = await session.get(User, 1)
        user.google_scopes = list(set(user.google_scopes + CALENDAR_SCOPES))

    async def calendar_request(method, path, token, **kwargs):
        if path == "/users/me/calendarList":
            return {
                "items": [{"id": "work@example.test", "summary": "Work", "accessRole": "owner"}]
            }
        assert path == "/freeBusy"
        data = kwargs["json"]
        return {
            "timeMin": data["timeMin"],
            "timeMax": data["timeMax"],
            "calendars": {"work@example.test": {"busy": []}},
        }

    monkeypatch.setattr(calendar_client, "_request", calendar_request)
    result = db_client.put(
        "/calendar/preferences",
        headers=auth_headers(1),
        json={
            "expected_version": 0,
            "preferences": {
                "timezone": "Australia/Melbourne",
                "calendar_ids": ["work@example.test"],
                "working_periods": [
                    {"weekday": day, "start_minute": 540, "end_minute": 1020} for day in range(7)
                ],
                "buffer_before_minutes": 0,
                "buffer_after_minutes": 0,
                "minimum_notice_minutes": 0,
                "default_duration_minutes": 30,
            },
        },
    )
    assert result.status_code == 200, result.text
    snapshot = capture(db_client, auth_headers)
    result = db_client.post(
        "/assistant/workflow-requests",
        headers=auth_headers(1),
        json={
            "schema_version": "1.0",
            "request_id": "three-outputs",
            "instruction": "Summarise this, find three times tomorrow, and draft a reply.",
            "context_snapshot_id": snapshot["context_snapshot_id"],
            "operations": ["summary", "schedule", "draft_reply"],
            "summary_in_draft": True,
            "schedule": {
                "operation": "suggest_slots",
                "expected_preferences_version": 1,
                "constraints": {"date": "tomorrow", "count": 3},
            },
            "draft_options": {"to": ["sender@example.test"], "reply_message_id": MID},
        },
    )
    assert result.status_code == 202, result.text
    await worker.run_once(db_sessionmaker, Model())
    task = db_client.get(
        "/assistant/tasks/" + result.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert task["state"] == "succeeded", task
    assert task["workflow"]["completed_steps"] == 3
    assert await _message_count(db_sessionmaker) == 0


def test_ui_capture_and_fact_lookup_live(setup, db_client, auth_headers):
    # The authoritative typed schema is exercised with provider message IDs.
    mapping = {
        "surface": "gmail_thread",
        "thread_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "visible_message_ids": [MID],
        "selected_message_ids": [MID],
        "schema_version": "1.0",
    }
    response = db_client.post(
        "/assistant/context-snapshots",
        headers=auth_headers(1),
        json={"schema_version": "1.1", "thread_id": TID, "ui_map": mapping},
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()
    response = db_client.get(
        "/entities",
        params={"context_snapshot_id": snapshot["context_snapshot_id"]},
        headers=auth_headers(1),
    )
    assert response.status_code == 200, response.text


async def test_typed_source_search_live(setup, db_client, auth_headers, db_sessionmaker):
    snapshot = capture(db_client, auth_headers)
    response = db_client.post(
        "/assistant/requests",
        headers=auth_headers(1),
        json={
            "schema_version": "1.0",
            "request_id": "typed-read",
            "continuation": None,
            "instruction": "Find Friday",
            "intent_hint": "other",
            "context_snapshot_id": snapshot["context_snapshot_id"],
            "read_options": {"operation": "search_mail", "query": "Friday"},
        },
    )
    assert response.status_code == 202, response.text
    model = Model()
    await worker.run_once(db_sessionmaker, model)
    task = db_client.get(
        "/assistant/tasks/" + response.json()["task_id"], headers=auth_headers(1)
    ).json()
    assert task["state"] == "succeeded", task
    assert not model.calls
    assert await _message_count(db_sessionmaker) == 0


async def test_negotiation_rechecks_live_source(
    setup, db_client, auth_headers, db_sessionmaker, monkeypatch
):
    from app.auth.google import CALENDAR_SCOPES
    from app.calendar import negotiations

    monkeypatch.setattr(negotiations, "get_session_factory", lambda: db_sessionmaker)
    async with db_sessionmaker.begin() as session:
        user = await session.get(User, 1)
        user.google_scopes = list(set(user.google_scopes + CALENDAR_SCOPES))
    capture(db_client, auth_headers)
    result = db_client.post(
        "/calendar/negotiations",
        headers=auth_headers(1),
        json={"request_id": "live-neg", "thread_id": TID, "expected_thread_version": 1},
    )
    assert result.status_code == 201, result.text
    identifier = result.json()["id"]
    assert (
        db_client.get("/calendar/negotiations/" + identifier, headers=auth_headers(1)).status_code
        == 200
    )
    setup.text = "The time has changed."
    result = db_client.get("/calendar/negotiations/" + identifier, headers=auth_headers(1))
    assert result.status_code == 409, result.text
    assert result.json()["error"]["code"] == "source_changed"
    before = len(setup.calls)
    result = db_client.get("/calendar/negotiations/" + identifier, headers=auth_headers(2))
    assert result.status_code == 404
    assert len(setup.calls) == before
    setup.denied = True
    result = db_client.post(
        "/calendar/negotiations/" + identifier + "/close",
        headers=auth_headers(1),
        json={"request_id": "close-offline", "expected_version": 1},
    )
    assert result.status_code == 200, result.text
    assert result.json()["state"] == "closed"
    assert len(setup.calls) == before
    assert await _message_count(db_sessionmaker) == 0


def test_cursor_owner_account_expiry_and_signature(setup, monkeypatch):
    from app.api.errors import ApiError
    from app.mail import search

    monkeypatch.setattr(search.time, "time", lambda: 1000)
    cursor = search.encode_cursor(1, 1, "scope", "next")
    assert search.decode_cursor(cursor, 1, 1, "scope") == "next"
    for owner, version, scope in ((2, 1, "scope"), (1, 2, "scope"), (1, 1, "other")):
        with pytest.raises(ApiError):
            search.decode_cursor(cursor, owner, version, scope)
    with pytest.raises(ApiError):
        search.decode_cursor(cursor[:-1], 1, 1, "scope")
    monkeypatch.setattr(search.time, "time", lambda: 1900)
    with pytest.raises(ApiError):
        search.decode_cursor(cursor, 1, 1, "scope")


def test_thread_list_preserves_cursor_and_no_store_contract(setup, db_client, auth_headers):
    response = db_client.get("/threads?days=7", headers=auth_headers(1))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["source"] == "live_gmail" and data["next_cursor"]
    assert data["persisted_email_content"] is False
    assert data["threads"][0]["needs_reply"] is None
    assert response.headers["cache-control"] == "no-store"
    response = db_client.get(
        "/threads", params={"days": 7, "cursor": data["next_cursor"]}, headers=auth_headers(1)
    )
    assert response.status_code == 200, response.text
    calls = [r for r in setup.calls if r.url.path.endswith("/messages")]
    assert len(calls) == 2 and calls[-1].url.params["pageToken"] == "next-page"
