"""T02 acceptance: real PostgreSQL state, deterministic Gmail races via HTTP fixtures."""

import asyncio
import base64
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.assistant.context import capture_thread
from app.db import repositories as repo
from app.db.models import ContextSnapshot, Message, Summary, Thread, User
from app.sync.gmail import GmailClient, GmailError
from app.sync.worker import SyncConflict, _message_values, _parse_sent_at, incremental_sync
from tests.conftest import needs_pg
from tests.test_sync_worker import _seed_user


def message(mid, millis="1000", *, sender="Me <me@x.com>", body="original", labels=None):
    return {
        "id": mid,
        "threadId": "thread",
        "internalDate": millis,
        "labelIds": labels or ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": f"Subject {mid}"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


class Mailbox:
    """Current detail reads plus an immutable history response for each sync attempt."""

    def __init__(self, messages, *, changed=(), after_list=None, fail_history=False):
        self.messages = {m["id"]: m for m in messages}
        self.changed = list(changed)
        self.after_list = after_list
        self.fail_history = fail_history
        self.calls = []

    async def handle(self, request):
        path = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((path, dict(request.url.params)))
        if path == "profile":
            return httpx.Response(200, json={"historyId": "100"})
        if path == "messages":
            refs = [{"id": mid} for mid in self.messages]
            if self.after_list:
                self.after_list(self)
            return httpx.Response(200, json={"messages": refs})
        if path == "history":
            if self.fail_history:
                return httpx.Response(503, text="private upstream response")
            return httpx.Response(
                200,
                json={
                    "historyId": "101",
                    "history": [
                        {"messagesAdded": [{"message": {"id": mid}} for mid in self.changed]}
                    ],
                },
            )
        if path in self.messages:
            return httpx.Response(200, json=self.messages[path])
        return httpx.Response(404)

    @property
    def transport(self):
        return httpx.MockTransport(self.handle)


async def sync(db, uid, box):
    async with db() as session:
        user = await session.get(User, uid)
        return await incremental_sync(session, user, "token", transport=box.transport)


@pytest.mark.parametrize(
    "sender,expected",
    [
        ("Me <ME@X.COM>", True),
        ("me@x.com <attacker@evil.test>", False),
        ("Other <notme@x.com>", False),
        ("Other <me@x.com.evil.test>", False),
        ("Alias <alias@x.com>", False),
        ("me@x.com, attacker@evil.test", False),
        ("malformed", False),
    ],
)
def test_sender_matches_parsed_mailbox_only(sender, expected):
    assert _message_values(message("m", sender=sender), "me@x.com")["is_from_user"] is expected


def test_duplicate_from_is_not_trusted_and_reply_headers_are_preserved():
    msg = message("m")
    msg["payload"]["headers"] += [
        {"name": "From", "value": "attacker@evil.test"},
        {"name": "Message-ID", "value": "<original@example.test>"},
        {"name": "References", "value": "<parent@example.test>"},
        {"name": "In-Reply-To", "value": "<parent@example.test>"},
        {"name": "Reply-To", "value": "Other <reply@example.test>"},
        {"name": "Cc", "value": "Team <team@example.test>"},
    ]
    parsed = _message_values(msg, "me@x.com")
    assert not parsed["is_from_user"]
    meta = parsed["reply_metadata"]
    assert meta["headers"]["message-id"] == ["<original@example.test>"]
    assert meta["headers"]["references"] == ["<parent@example.test>"]
    assert meta["headers"]["in-reply-to"] == ["<parent@example.test>"]
    assert meta["addresses"]["reply-to"] == ["reply@example.test"]
    assert meta["addresses"]["cc"] == ["team@example.test"]


@pytest.mark.parametrize("date", [None, "garbage", "Fri, 01 Jan 2021 10:00:00 -0000"])
def test_missing_bad_or_unzoned_date_falls_back_to_provider(date):
    headers = [{"name": "Date", "value": date}] if date else []
    assert _parse_sent_at({"headers": headers}, "1000") == datetime.fromtimestamp(1, UTC)
    assert _parse_sent_at({}, "9" * 100) is None


@needs_pg
@pytest.mark.asyncio
async def test_stable_order_metadata_versions_and_context(db_sessionmaker):
    uid = await _seed_user(db_sessionmaker)
    newest = message("m3", "2000")
    # A misleading Date does not determine the thread head.
    older = message("m1", "1000")
    older["payload"]["headers"].append({"name": "Date", "value": "Fri, 01 Jan 2038 00:00:00 +0000"})
    box = Mailbox([newest, message("m2", "2000"), older, message("m0", None)])
    await sync(db_sessionmaker, uid, box)
    async with db_sessionmaker() as session:
        thread = (await session.execute(select(Thread))).scalar_one()
        assert (thread.last_msg_id, thread.subject, thread.version) == ("m3", "Subject m3", 1)
        rows = await repo.thread_messages(session, thread.id)
        assert [m.gmail_msg_id for m in rows] == ["m0", "m1", "m2", "m3"]
        snapshot = await capture_thread(session, uid, "thread")
        assert [m["message_id"] for m in snapshot.payload["messages"]] == ["m0", "m1", "m2", "m3"]
        assert snapshot.payload["thread_version"] == 1
        await session.commit()
    # Same source data, even in a different ingest order, does not increase version.
    box.changed = ["m1", "m3", "m2", "m0"]
    report = await sync(db_sessionmaker, uid, box)
    assert report.threads_touched == 0
    async with db_sessionmaker() as session:
        assert (await session.execute(select(Thread.version))).scalar_one() == 1
    box.messages["m1"] = message("m1", "1000", body="edited")
    await sync(db_sessionmaker, uid, box)
    async with db_sessionmaker() as session:
        thread = (await session.execute(select(Thread))).scalar_one()
        assert (thread.last_msg_id, thread.version) == ("m3", 2)
        saved = (await session.execute(select(ContextSnapshot))).scalar_one()
        assert saved.payload["thread_version"] == 1  # historical snapshots never mutate


@needs_pg
@pytest.mark.asyncio
async def test_backfill_replays_arrival_and_deletion_during_scan(db_sessionmaker):
    uid = await _seed_user(db_sessionmaker)

    def arrive(box):
        del box.messages["deleted"]
        box.messages["arrived"] = message("arrived", "2000")
        box.changed = ["arrived", "deleted"]

    box = Mailbox([message("original"), message("deleted")], after_list=arrive)
    await sync(db_sessionmaker, uid, box)
    assert [c[0] for c in box.calls][:2] == ["profile", "messages"]
    assert (
        next(params for path, params in box.calls if path == "history")["startHistoryId"] == "100"
    )
    async with db_sessionmaker() as session:
        assert set((await session.execute(select(Message.gmail_msg_id))).scalars()) == {
            "original",
            "arrived",
        }
        assert (await session.get(User, uid)).gmail_history_id == "101"


@needs_pg
@pytest.mark.asyncio
async def test_deletions_and_spam_invalidate_cache_but_retain_saved_tasks(db_sessionmaker):
    uid = await _seed_user(db_sessionmaker)
    box = Mailbox([message("old"), message("new", "2000")])
    await sync(db_sessionmaker, uid, box)
    async with db_sessionmaker() as session:
        thread = (await session.execute(select(Thread))).scalar_one()
        session.add(Summary(user_id=uid, thread_id=thread.id, last_msg_id="new", body="stale"))
        await capture_thread(session, uid, "thread")
        await session.commit()
    box.changed = ["new", "old"]
    del box.messages["new"]
    box.messages["old"]["labelIds"] = ["SPAM"]
    await sync(db_sessionmaker, uid, box)
    async with db_sessionmaker() as session:
        assert not (await session.execute(select(Message))).scalars().all()
        assert not (await session.execute(select(Summary))).scalars().all()
        thread = (await session.execute(select(Thread))).scalar_one()
        assert thread.last_msg_id is None and thread.version == 2
        assert (await session.execute(select(ContextSnapshot))).scalar_one()


@needs_pg
@pytest.mark.asyncio
async def test_failed_replay_never_advances_cursor_or_changes_mail(db_sessionmaker):
    uid = await _seed_user(db_sessionmaker)
    with pytest.raises(GmailError):
        await sync(db_sessionmaker, uid, Mailbox([message("m")], fail_history=True))
    async with db_sessionmaker() as session:
        user = await session.get(User, uid)
        assert user.gmail_history_id is None and user.sync_version == 0
        assert not (await session.execute(select(Message))).scalars().all()


@needs_pg
@pytest.mark.asyncio
async def test_concurrent_sync_loser_cannot_overwrite_winner(db_sessionmaker):
    uid = await _seed_user(db_sessionmaker)
    reached, release = asyncio.Event(), asyncio.Event()
    stale = Mailbox([message("stale")])
    original = stale.handle

    async def pause(request):
        if request.url.path.endswith("/profile"):
            reached.set()
            await release.wait()
        return await original(request)

    stale.handle = pause
    attempt = asyncio.create_task(sync(db_sessionmaker, uid, stale))
    try:
        await asyncio.wait_for(reached.wait(), 5)
        # Completes while the earlier Google request is blocked: no network-held DB lock.
        await asyncio.wait_for(sync(db_sessionmaker, uid, Mailbox([message("winner")])), 5)
    finally:
        release.set()
    with pytest.raises(SyncConflict):
        await attempt
    async with db_sessionmaker() as session:
        assert list((await session.execute(select(Message.gmail_msg_id))).scalars()) == ["winner"]
        assert (await session.get(User, uid)).sync_version == 1


@needs_pg
@pytest.mark.asyncio
async def test_summary_publication_rejects_changed_source_version(db_sessionmaker):
    uid = await _seed_user(db_sessionmaker)
    box = Mailbox([message("m")])
    await sync(db_sessionmaker, uid, box)
    box.messages["m"] = message("m", body="updated")
    box.changed = ["m"]
    await sync(db_sessionmaker, uid, box)
    async with db_sessionmaker() as session:
        thread = (await session.execute(select(Thread))).scalar_one()
        assert not await repo.store_summary(
            session,
            user_id=uid,
            thread_pk=thread.id,
            last_msg_id="m",
            body="stale",
            model_used="test",
            expected_version=1,
        )
        assert not (await session.execute(select(Summary))).scalars().all()


@pytest.mark.asyncio
async def test_history_paginates_and_includes_label_and_delete_changes():
    def handle(request):
        if "pageToken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "historyId": "102",
                    "nextPageToken": "next",
                    "history": [
                        {
                            "labelsAdded": [{"message": {"id": "a"}}],
                            "messagesDeleted": [{"message": {"id": "b"}}],
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "historyId": "102",
                "history": [
                    {
                        "labelsRemoved": [{"message": {"id": "a"}}],
                        "messagesAdded": [{"message": {"id": "c"}}],
                    }
                ],
            },
        )

    changed, cursor = await GmailClient("token", httpx.MockTransport(handle)).history_since("100")
    assert set(changed) == {"a", "b", "c"} and len(changed) == 3
    assert cursor == "102"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["history", "messages"])
async def test_repeated_pagination_token_fails_closed(method):
    client = GmailClient(
        "token",
        httpx.MockTransport(
            lambda request: httpx.Response(200, json={"historyId": "101", "nextPageToken": "same"})
        ),
    )
    with pytest.raises(GmailError, match="repeated"):
        if method == "history":
            await client.history_since("100")
        else:
            await client.list_all_message_ids()


@needs_pg
@pytest.mark.asyncio
async def test_expired_cursor_rebuilds_and_removes_old_cached_rows(db_sessionmaker):
    uid = await _seed_user(db_sessionmaker)
    await sync(db_sessionmaker, uid, Mailbox([message("old")]))
    box = Mailbox([message("fresh")])
    original = box.handle

    async def expired(request):
        if (
            request.url.path.endswith("/history")
            and request.url.params.get("startHistoryId") == "101"
        ):
            return httpx.Response(404)
        return await original(request)

    box.handle = expired
    result = await sync(db_sessionmaker, uid, box)
    assert result.mode == "backfill"
    async with db_sessionmaker() as session:
        assert list((await session.execute(select(Message.gmail_msg_id))).scalars()) == ["fresh"]
        assert (await session.get(User, uid)).sync_version == 2


@needs_pg
@pytest.mark.asyncio
async def test_full_reconciliation_is_owner_scoped(db_sessionmaker):
    uid = await _seed_user(db_sessionmaker)
    async with db_sessionmaker() as session:
        other = User(google_sub="other-user", email="other@example.test")
        session.add(other)
        await session.commit()
        other_id = other.id
    await sync(db_sessionmaker, uid, Mailbox([message("same-id")]))
    await sync(db_sessionmaker, other_id, Mailbox([message("same-id", body="other user")]))
    async with db_sessionmaker() as session:
        user = await session.get(User, uid)
        user.gmail_history_id = None
        await session.commit()
    await sync(db_sessionmaker, uid, Mailbox([]))
    async with db_sessionmaker() as session:
        remaining = (await session.execute(select(Message))).scalar_one()
        assert remaining.user_id == other_id and remaining.body_clean == "other user"
        assert (
            await session.execute(select(Thread.version).where(Thread.user_id == other_id))
        ).scalar_one() == 1


@needs_pg
@pytest.mark.parametrize(
    "failure,status,code",
    [
        (GmailError("private text", 401), 401, "gmail_reauth_required"),
        (GmailError("private text", 429), 503, "gmail_sync_unavailable"),
        (SyncConflict(), 409, "sync_conflict"),
    ],
)
def test_sync_api_errors_are_sanitized(
    db_client, db_sessionmaker, auth_headers, monkeypatch, failure, status, code
):
    from app.api.routes import sync as route

    uid = asyncio.run(_seed_user(db_sessionmaker))

    async def token(*args):
        return "token"

    async def fail(*args):
        raise failure

    monkeypatch.setattr(route.auth_service, "get_valid_access_token", token)
    monkeypatch.setattr(route.worker, "incremental_sync", fail)
    response = db_client.post("/sync", headers=auth_headers(uid))
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "private text" not in response.text


@needs_pg
@pytest.mark.asyncio
async def test_legacy_summary_releases_lock_and_refuses_stale_completion(
    db_sessionmaker, monkeypatch
):
    import app.orchestrator.orchestrator as orch
    from app.api.errors import ApiError
    from app.model_client.client import GenResult

    uid = await _seed_user(db_sessionmaker)
    box = Mailbox([message("m")])
    await sync(db_sessionmaker, uid, box)

    class ConcurrentModel:
        async def stream(self, *args, **kwargs):
            async def tokens():
                box.changed = ["m"]
                box.messages["m"] = message("m", body="changed during model call")
                await asyncio.wait_for(sync(db_sessionmaker, uid, box), 5)
                yield "summary from old context"

            return tokens(), GenResult("fake", "fake")

    monkeypatch.setattr(orch, "get_model_client", lambda: ConcurrentModel())
    events = []
    async with db_sessionmaker() as session:
        with pytest.raises(ApiError) as failure:
            async for event in orch.summarise_thread(session, uid, "thread"):
                events.append(event.kind)
        assert failure.value.code == "context_changed"
    assert events == ["token"]  # no successful done event or persisted stale cache
    async with db_sessionmaker() as session:
        assert not (await session.execute(select(Summary))).scalars().all()
