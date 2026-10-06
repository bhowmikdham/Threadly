"""A connection change must not strand the next chat behind inaccessible history."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.calendar import permissions
from app.config import get_settings
from app.conversation import store
from app.db.models import Conversation, User
from app.schemas.conversation import CalendarApprovalSetting, ConversationTurn


@pytest.fixture()
async def accounts(db_sessionmaker, monkeypatch):
    monkeypatch.setenv("CONVERSATION_MAX_ROWS_PER_USER", "5")
    monkeypatch.setenv("CONVERSATION_MAX_RETAINED_TURNS", "20")
    monkeypatch.setenv("CONVERSATION_MAX_ACTIVE_PER_USER", "2")
    get_settings.cache_clear()
    async with db_sessionmaker.begin() as session:
        session.add_all(
            [
                User(id=1, email="one@example.test", google_sub="one", google_account_version=2),
                User(id=2, email="two@example.test", google_sub="two", google_account_version=2),
            ]
        )
    yield
    get_settings.cache_clear()


def turn(cid=None):
    return ConversationTurn(
        conversation_id=cid or str(uuid4()),
        request_id=str(uuid4()),
        expected_version=0,
        instruction="Hello",
    )


async def seed(
    factory, *, count, owner=1, account_version=1, version=2, pending=False, active=False
):
    ids = []
    async with factory.begin() as session:
        for _ in range(count):
            cid = str(uuid4())
            ids.append(cid)
            session.add(
                Conversation(
                    id=cid,
                    user_id=owner,
                    account_version=account_version,
                    version=version,
                    state_enc=store.encode({"history": [], "refs": {}, "result_order": []}),
                    expires_at=datetime.now(UTC) + timedelta(days=2),
                    pending_request_id=str(uuid4()) if pending else None,
                    pending_hash="a" * 64 if pending else None,
                    lease_id=str(uuid4()) if active else None,
                    lease_until=datetime.now(UTC) + timedelta(minutes=1) if active else None,
                    calendar_approval_mode="always",
                    calendar_approval_version=1,
                )
            )
    return ids


async def claim(factory, request):
    async with factory.begin() as session:
        return await store.claim(session, 1, request)


@pytest.mark.parametrize("entrypoint", ["turn", "calendar_setting"])
async def test_stale_saturated_history_is_preserved_without_blocking_new_chat(
    accounts, db_sessionmaker, entrypoint
):
    stale = await seed(db_sessionmaker, count=50, pending=True)
    async with db_sessionmaker() as session:
        before = (
            await session.execute(
                select(Conversation.id, Conversation.state_enc, Conversation.expires_at)
                .where(Conversation.id.in_(stale))
                .order_by(Conversation.id)
            )
        ).all()
    request = turn()
    if entrypoint == "calendar_setting":
        result = await permissions.set_mode(
            1,
            request.conversation_id,
            CalendarApprovalSetting(mode="ask", expected_version=0),
            factory=db_sessionmaker,
        )
        assert result["mode"] == "ask"
    row, _, _, _ = await claim(db_sessionmaker, request)
    assert row.account_version == 2 and row.calendar_approval_mode == "ask"
    async with db_sessionmaker() as session:
        after = (
            await session.execute(
                select(Conversation.id, Conversation.state_enc, Conversation.expires_at)
                .where(Conversation.id.in_(stale))
                .order_by(Conversation.id)
            )
        ).all()
        assert after == before
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 51
        for owner, code in [(1, "conversation_account_changed"), (2, "conversation_not_found")]:
            with pytest.raises(ApiError) as exc:
                await store.owned(session, owner, stale[0])
            assert exc.value.code == code
    with pytest.raises(ApiError) as exc:
        await permissions.set_mode(
            1,
            stale[0],
            CalendarApprovalSetting(mode="ask", expected_version=1),
            factory=db_sessionmaker,
        )
    assert exc.value.code == "conversation_account_changed"


@pytest.mark.parametrize("entrypoint", ["turn", "calendar_setting"])
async def test_current_chat_row_limit_still_applies_at_exact_boundary(
    accounts, db_sessionmaker, entrypoint
):
    await seed(db_sessionmaker, count=50)
    await seed(db_sessionmaker, count=4, account_version=2, version=0)

    async def create(request):
        if entrypoint == "turn":
            await claim(db_sessionmaker, request)
        else:
            await permissions.set_mode(
                1,
                request.conversation_id,
                CalendarApprovalSetting(mode="ask", expected_version=0),
                factory=db_sessionmaker,
            )

    await create(turn())
    with pytest.raises(ApiError) as exc:
        await create(turn())
    assert exc.value.code == "conversation_history_limit"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Conversation)) == 55


@pytest.mark.parametrize("pending", [False, True])
async def test_current_turn_budget_counts_completed_and_pending(accounts, db_sessionmaker, pending):
    await seed(db_sessionmaker, count=50, pending=True)
    await seed(
        db_sessionmaker, count=1, account_version=2, version=19 if pending else 20, pending=pending
    )
    with pytest.raises(ApiError) as exc:
        await claim(db_sessionmaker, turn())
    assert exc.value.code == "conversation_turn_limit"


async def test_current_pending_retry_survives_full_turn_budget(accounts, db_sessionmaker):
    await seed(db_sessionmaker, count=50, pending=True)
    await seed(db_sessionmaker, count=1, account_version=2, version=19)
    request = turn()
    row, _, lease, _ = await claim(db_sessionmaker, request)
    async with db_sessionmaker.begin() as session:
        current = await session.get(Conversation, row.id)
        current.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    retried, _, new_lease, _ = await claim(db_sessionmaker, request)
    assert retried.id == row.id and new_lease != lease


async def test_stale_active_leases_still_apply_to_account_capacity(accounts, db_sessionmaker):
    await seed(db_sessionmaker, count=2, pending=True, active=True)
    with pytest.raises(ApiError) as exc:
        await claim(db_sessionmaker, turn())
    assert exc.value.code == "conversation_capacity"


async def test_another_owner_cannot_consume_current_connection_budget(accounts, db_sessionmaker):
    await seed(db_sessionmaker, count=50, owner=2, account_version=2, pending=True, active=True)
    row, _, _, _ = await claim(db_sessionmaker, turn())
    assert row.user_id == 1


async def test_parallel_new_chats_cannot_overrun_current_row_limit(accounts, db_sessionmaker):
    await seed(db_sessionmaker, count=50)
    await seed(db_sessionmaker, count=4, account_version=2, version=0)
    results = await asyncio.gather(
        claim(db_sessionmaker, turn()), claim(db_sessionmaker, turn()), return_exceptions=True
    )
    errors = [result for result in results if isinstance(result, ApiError)]
    assert len(errors) == 1 and errors[0].code == "conversation_history_limit"
    assert sum(isinstance(result, tuple) for result in results) == 1
