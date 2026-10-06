"""Owned legacy pending-turn recovery, exact-key tombstones and late-commit fencing."""
# ruff: noqa: F811

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.calendar import event_creation
from app.conversation import recovery, service, store
from app.db.models import ActionJob, AssistantAction, AssistantTask, Conversation
from app.schemas.conversation import PrepareCalendarEvent, RecoverConversation
from tests.test_calendar_creation import ARGS, configured, ready, run, turn  # noqa: F401
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool


def command(request, operation="cancel", **fields):
    return RecoverConversation(
        pending_request_id=request.request_id,
        expected_version=request.expected_version,
        operation=operation,
        **fields,
    )


async def strand(factory, request, *, release=True):
    async with factory.begin() as db:
        _, state, lease, _ = await store.claim(db, 1, request)
    if release:
        async with factory.begin() as db:
            await store.release_failed(db, 1, request.conversation_id, lease)
    return state, lease


async def test_cancel_legacy_empty_pending_keeps_exact_receipt_and_next_question(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn("book 2 pm tmrw for doctors appointment")
    await strand(db_sessionmaker, request)
    pending = await service.get(1, request.conversation_id, factory=db_sessionmaker)
    assert pending["pending_recovery"] == {"active": False, "has_saved_result": False}
    result = await recovery.recover(
        1, request.conversation_id, command(request), factory=db_sessionmaker
    )
    assert (await service.get(1, request.conversation_id, factory=db_sessionmaker))[
        "pending_recovery"
    ] is None
    assert result["version"] == 1 and result["error_code"] == "conversation_request_cancelled"
    assert (
        await recovery.recover(
            1, request.conversation_id, command(request), factory=db_sessionmaker
        )
        == result
    )
    replay = await run(db_sessionmaker, request)
    assert replay["error_code"] == "conversation_request_cancelled"
    async with db_sessionmaker() as db:
        row = await db.get(Conversation, request.conversation_id)
        assert row.pending_request_id is None and row.lease_id is None
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    fresh = turn(
        "am i free at 2 pm tmrw?", conversation_id=request.conversation_id, expected_version=1
    )
    checked = await service.turn(
        1,
        fresh,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "check_time_availability",
                subject="self",
                date={"kind": "relative", "offset_days": 1},
                date_source="tmrw",
                at_time="14:00",
                at_time_source="2 pm",
            )
        ),
    )
    assert (
        checked["version"] == 2
        and checked["calendar_tools"]["operation"] == "check_time_availability"
    )


async def test_recovery_route_enforces_owner_and_payload_version(
    configured, db_sessionmaker, db_client, auth_headers, monkeypatch
):
    monkeypatch.setattr(recovery, "get_session_factory", lambda: db_sessionmaker)
    request = turn()
    await strand(db_sessionmaker, request)
    path = f"/assistant/conversations/{request.conversation_id}/recover"
    body = command(request).model_dump()
    assert db_client.post(path, json=body).status_code == 401
    assert db_client.post(path, json=body, headers=auth_headers(2)).status_code == 404
    assert (
        db_client.post(
            path, json={**body, "expected_version": 7}, headers=auth_headers(1)
        ).status_code
        == 409
    )
    assert (
        db_client.post(
            path, json={**body, "pending_request_id": str(uuid4())}, headers=auth_headers(1)
        ).status_code
        == 409
    )
    result = db_client.post(path, json=body, headers=auth_headers(1))
    assert result.status_code == 200 and result.json()["version"] == 1


async def test_active_request_cannot_be_cancelled(configured, db_sessionmaker):
    request = turn()
    await strand(db_sessionmaker, request, release=False)
    pending = await service.get(1, request.conversation_id, factory=db_sessionmaker)
    assert pending["pending_recovery"] == {"active": True, "has_saved_result": False}
    with pytest.raises(ApiError) as error:
        await recovery.recover(
            1, request.conversation_id, command(request), factory=db_sessionmaker
        )
    assert error.value.code == "conversation_busy"


async def test_unknown_child_work_is_not_discarded(configured, db_sessionmaker):
    request = turn()
    await strand(db_sessionmaker, request)
    async with db_sessionmaker.begin() as db:
        db.add(
            AssistantTask(
                id=str(uuid4()),
                user_id=1,
                request_id="chat-" + request.request_id,
                request_hash="0" * 64,
                instruction="Synthetic draft",
                intent_hint="compose",
                state="queued",
                version=1,
                latest_sequence=1,
                release={},
            )
        )
    with pytest.raises(ApiError) as error:
        await recovery.recover(
            1, request.conversation_id, command(request), factory=db_sessionmaker
        )
    assert error.value.code == "conversation_work_exists"
    async with db_sessionmaker() as db:
        assert (
            await db.get(Conversation, request.conversation_id)
        ).pending_request_id == request.request_id


async def test_saved_event_is_recovered_without_approval_or_duplicate(
    configured, db_sessionmaker, monkeypatch
):
    await ready(db_sessionmaker)
    request = turn()

    async def fail_after_checkpoint(*args, **kwargs):
        raise RuntimeError("simulated connection lost after commit")

    with monkeypatch.context() as m:
        m.setattr(store, "complete", fail_after_checkpoint)
        with pytest.raises(RuntimeError):
            await run(db_sessionmaker, request)
    with pytest.raises(ApiError) as error:
        await recovery.recover(
            1, request.conversation_id, command(request), factory=db_sessionmaker
        )
    assert error.value.code == "conversation_result_available"
    pending = await service.get(1, request.conversation_id, factory=db_sessionmaker)
    assert pending["pending_recovery"] == {"active": False, "has_saved_result": True}
    result = await recovery.recover(
        1, request.conversation_id, command(request, "recover"), factory=db_sessionmaker
    )
    assert result["calendar_action"]["state"] == "proposed" and result["version"] == 1
    replay = await run(db_sessionmaker, request)
    assert replay["calendar_action_id"] == result["calendar_action_id"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["history"][-1]["user"] == "" and state["history"][-1]["recovered"]
    assert not any(c.url.path.endswith("/events") for c in configured[0])


async def test_cancellation_fences_late_calendar_candidate_transaction(configured, db_sessionmaker):
    await ready(db_sessionmaker)
    request = turn()
    state, lease = await strand(db_sessionmaker, request)
    runtime = service.Runtime(1, request, state, db_sessionmaker, lease)
    await recovery.recover(1, request.conversation_id, command(request), factory=db_sessionmaker)
    with pytest.raises(ApiError) as error:
        await event_creation.prepare(runtime, PrepareCalendarEvent(**ARGS))
    assert error.value.code == "conversation_lease_lost"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(AssistantTask)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_concurrent_recovery_finishes_once(configured, db_sessionmaker):
    request = turn()
    await strand(db_sessionmaker, request)
    first, second = await asyncio.gather(
        *[
            recovery.recover(1, request.conversation_id, command(request), factory=db_sessionmaker)
            for _ in range(2)
        ]
    )
    assert first == second and first["version"] == 1
    async with db_sessionmaker() as db:
        row = await db.get(Conversation, request.conversation_id)
        assert row.version == 1 and len(store.decode(row)["receipts"]) == 1


async def test_recovery_replays_a_normally_completed_turn_with_issued_id(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn()
    original = await run(db_sessionmaker, request)
    recovered = await recovery.recover(
        1, request.conversation_id, command(request, "recover"), factory=db_sessionmaker
    )
    assert recovered["recovered_request_id"] == request.request_id
    assert recovered["version"] == original["version"] == 1
    assert recovered["calendar_action_id"] == original["calendar_action_id"]
    assert recovered["calendar_action"]["state"] == "proposed"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
