"""Whole-chat regressions; isolated PostgreSQL and fake model/Google adapters."""
# ruff: noqa: F811

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from app.api.errors import ApiError
from app.auth.crypto import decrypt_token
from app.calendar import event_choices
from app.conversation import email_draft, memory, service, store
from app.conversation.runtime import Runtime
from app.db.models import ActionJob, AssistantAction, Conversation, ConversationExchange, User
from app.operations import retention
from app.schemas.conversation import ReadEmail, RecallConversation
from tests.test_calendar_creation import configured, ready, setup, turn  # noqa: F401
from tests.test_conversation import Model, tool

REPORTED = "i have a meeting with gaurav tmrw at 9 pm AEST , i need you to create an event for that"


class ObservedModel(Model):
    async def decide(self, system, messages, tools):
        self.messages = deepcopy(messages)
        return await super().decide(system, messages, tools)


async def next_turn(factory, previous, text, *calls):
    request = turn(
        text, conversation_id=previous["conversation_id"], expected_version=previous["version"]
    )
    model = ObservedModel(*calls)
    response = await service.turn(1, request, factory=factory, model=model)
    return response, model


async def test_reported_aest_request_and_repeated_answers_keep_the_same_event(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    request = turn(REPORTED)
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                title="meeting with gaurav",
                date={"kind": "relative", "offset_days": 1},
                date_source="tmrw",
                time="21:00",
                time_source="9 pm AEST",
            )
        ),
    )
    assert result["kind"] == "calendar_event", result
    action = result["calendar_action"]
    event = action["preview"]["event"]
    assert event["start"]["timeZone"] == "Etc/GMT-10"
    instant = datetime.fromisoformat(event["start"]["dateTime"])
    assert instant.astimezone(UTC).hour == 11  # 21:00 AEST; Melbourne would be 10 UTC.
    for text, values in (
        ("9 PM", {"time": "21:00", "time_source": "9 PM"}),
        ("Meeting with gaurav", {"title": "Meeting with gaurav"}),
    ):
        result, _ = await next_turn(
            db_sessionmaker,
            result,
            text,
            tool(
                "prepare_calendar_event",
                continue_previous=True,
                **values,
            ),
        )
        assert result.get("calendar_action_id") == action["action_id"], result
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    assert not any(call.url.path.endswith("/events") for call in configured[0])


async def test_ambiguous_time_keeps_title_date_and_zone_across_a_drafting_detour(
    configured, db_sessionmaker
):
    await ready(db_sessionmaker)
    first = turn("Create meeting with gaurav tmrw at 9 AEST")
    result = await service.turn(
        1,
        first,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                title="meeting with gaurav",
                date={"kind": "relative", "offset_days": 1},
                date_source="tmrw",
                time="21:00",
                time_source="9 AEST",
            )
        ),
    )
    assert result["kind"] == "clarification" and "AM or PM" in result["text"]
    result, _ = await next_turn(
        db_sessionmaker,
        result,
        "Draft an email to Alex",
        tool(
            "prepare_email_draft",
            recipient="Alex",
        ),
    )
    assert result["text"] == "What would you like to say?"
    result, model = await next_turn(
        db_sessionmaker,
        result,
        "For the Calendar meeting, 9 PM",
        tool(
            "prepare_calendar_event",
            continue_previous=True,
            time="21:00",
            time_source="9 PM",
        ),
    )
    context = model.contexts[0]
    assert context["pending_email_draft"]["recipient"] == "Alex"
    assert context["pending_calendar_event"]["arguments"]["title"] == "meeting with gaurav"
    assert result["kind"] == "calendar_event", result
    assert result["calendar_action"]["preview"]["event"]["start"]["timeZone"] == "Etc/GMT-10"
    restored = await service.get(1, first.conversation_id, factory=db_sessionmaker)
    assert restored["version"] == 3
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, first.conversation_id))
        assert state["email_draft_goal"]["recipient"] == "Alex"
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0


async def test_old_turn_retrieval_survives_context_eviction_and_is_idempotent(
    configured, db_sessionmaker
):
    request = turn("My project code for this chat is ALPHA-47.")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "respond",
                kind="message",
                text="Noted.",
            )
        ),
    )
    for _ in range(14):
        result, _ = await next_turn(
            db_sessionmaker,
            result,
            "Thanks",
            tool(
                "respond",
                kind="message",
                text="You're welcome.",
            ),
        )
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert len(state["history"]) == 12
        assert not any("ALPHA-47" in e["user"] for e in state["history"])
        assert await db.scalar(select(func.count()).select_from(ConversationExchange)) == 15
    result, model = await next_turn(
        db_sessionmaker,
        result,
        "What was my project code?",
        tool(
            "recall_conversation",
            query="ALPHA-47",
        ),
        tool("respond", kind="message", text="You gave ALPHA-47."),
    )
    observation = model.messages[-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert observation["exchanges"][0]["user"] == request.instruction
    assert model.contexts[0]["chat_memory"]["completed_turns"] == 15
    assert result["kind"] == "message"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0


async def test_recall_is_scoped_to_chat_owner_account_and_expiry(configured, db_sessionmaker):
    request = turn("My project code is PRIVATE-47.")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "respond",
                kind="message",
                text="Noted.",
            )
        ),
    )
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, request.conversation_id))
    runtime = Runtime(2, request, state, db_sessionmaker)
    with pytest.raises(ApiError) as failure:
        await memory.recall(runtime, RecallConversation())
    assert failure.value.status == 404
    runtime.owner = 1
    runtime.request = request.model_copy(update={"conversation_id": str(uuid4())})
    with pytest.raises(ApiError):
        await memory.recall(runtime, RecallConversation())
    runtime.request = request
    async with db_sessionmaker.begin() as db:
        user = await db.get(User, 1)
        user.google_account_version += 1
    with pytest.raises(ApiError) as failure:
        await memory.recall(runtime, RecallConversation())
    assert failure.value.code == "conversation_account_changed"
    async with db_sessionmaker.begin() as db:
        user = await db.get(User, 1)
        user.google_account_version -= 1
        row = await db.get(Conversation, result["conversation_id"])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ApiError) as failure:
        await memory.recall(runtime, RecallConversation())
    assert failure.value.code == "conversation_not_found"


async def test_calendar_intent_survives_fifteen_minutes_but_not_chat_retention(
    configured, db_sessionmaker, monkeypatch
):
    await ready(db_sessionmaker)
    request = turn("Create Focus tmrw")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_calendar_event",
                title="Focus",
                date={"kind": "relative", "offset_days": 1},
                date_source="tmrw",
            )
        ),
    )
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, result["conversation_id"]))

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(minutes=30)

    monkeypatch.setattr(event_choices, "datetime", Later)
    assert event_choices.pending(state)["arguments"]["title"] == "Focus"
    state["calendar_event_request"]["expires_at"] = datetime.now(UTC).isoformat()
    assert event_choices.pending(state) is None


async def archived_chat(factory, entries):
    request = turn("Review earlier context")
    async with factory.begin() as db:
        row, state, _, _ = await store.claim(db, 1, request)
        state["history"] = entries
        await memory.persist_state(db, row.id, state)
        store.compact(state)
        row.state_enc = store.encode(state)
        row.version = len(entries)
        row.lease_id = row.lease_until = row.pending_request_id = row.pending_hash = None
    return Runtime(1, request, state, factory)


def exchange(version, user, **values):
    return {
        "version": version,
        "request_id": str(uuid4()),
        "user": user,
        "assistant": "Understood.",
        "kind": "message",
        **values,
    }


async def test_retrieval_index_exposes_later_corrections_and_bounded_pagination(
    configured, db_sessionmaker
):
    entries = [exchange(i, f"Unrelated turn {i}") for i in range(1, 46)]
    entries[0]["user"] = "The first idea was an orange fox."
    entries[9]["user"] = "The project codename is ALPHA."
    entries[10]["user"] = "Actually, call it BETA instead."
    runtime = await archived_chat(db_sessionmaker, entries)
    found = await memory.recall(runtime, RecallConversation(query="codename"))
    assert [e["version"] for e in found["exchanges"]] == [10]
    assert any(e["version"] == 11 and "BETA" in e["user_excerpt"] for e in found["turn_index"])
    assert found["next_before_version"] == 6
    revised = await memory.recall(runtime, RecallConversation(versions=[10, 11]))
    assert [e["version"] for e in revised["exchanges"]] == [10, 11]
    assert revised["next_before_version"] is None
    older = await memory.recall(runtime, RecallConversation(before_version=6, query="orange"))
    assert older["exchanges"][0]["version"] == 1
    assert len(json.dumps(found, ensure_ascii=False)) < memory.RESULT_CHARS + 1000
    assert not runtime.state.get("active_goal")


async def test_archive_is_idempotent_legacy_backfill_and_an_owned_other_chat_is_empty(
    configured, db_sessionmaker
):
    entries = [exchange(i, "Private unstructured detail") for i in range(1, 3)]
    runtime = await archived_chat(db_sessionmaker, entries)
    async with db_sessionmaker.begin() as db:
        await db.execute(delete(ConversationExchange))
        row = await db.get(Conversation, runtime.request.conversation_id)
        state = store.decode(row)
        state.pop("archived_through_version")
        state.pop("archived_from_version")
        state["refs"]["selected"] = {"thread_id": "currently-selected-not-historical"}
        row.state_enc = store.encode(state)
    retry = runtime.request.model_copy(update={"expected_version": 2})
    async with db_sessionmaker.begin() as db:
        row, state, lease, _ = await store.claim(db, 1, retry)
        await memory.persist_state(db, row.id, state)
        await store.release_failed(db, 1, row.id, lease)
        assert await db.scalar(select(func.count()).select_from(ConversationExchange)) == 2
        old = await db.get(ConversationExchange, (row.id, 1))
        assert json.loads(decrypt_token(old.payload_enc))["source_references"] == {}
    # Even a stale in-memory object cannot mix one owned chat's fallback with another.
    other = turn()
    async with db_sessionmaker.begin() as db:
        await store.claim(db, 1, other)
    runtime.request = other
    found = await memory.recall(runtime, RecallConversation())
    assert found["exchanges"] == [] and found["turn_index"] == []


async def test_archive_retains_identity_only_and_redacts_historical_calendar_observation(
    configured, db_sessionmaker
):
    request = turn()
    previous_handle = "history-123456-" + "x" * 26
    async with db_sessionmaker.begin() as db:
        row, state, _, _ = await store.claim(db, 1, request)
        state["refs"] = {
            "selected": {
                "thread_id": "synthetic-thread",
                "message_id": "synthetic-message",
                "snippet": "Do not archive this provider snippet.",
            }
        }
        state["refs"][previous_handle] = {"thread_id": "synthetic-earlier-thread"}
        state["history"] = [
            exchange(
                1,
                "What is on my calendar?",
                assistant="Private provider event title",
                source="calendar_tools",
                context_references=[previous_handle],
            )
        ]
        await memory.persist_state(
            db, row.id, state, current_request_id=state["history"][-1]["request_id"]
        )
        row.state_enc = store.encode(state)
        row.version = 1
    runtime = Runtime(1, request, state, db_sessionmaker)
    found = await memory.recall(runtime, RecallConversation())
    assert "Private provider event title" not in json.dumps(found)
    handles = found["exchanges"][0]["source_references"]
    assert len(handles) == 2 and all(ReadEmail(reference=handle) for handle in handles)
    assert runtime.state["refs"][handles[0]] == {
        "thread_id": "synthetic-thread",
        "message_id": "synthetic-message",
    }
    async with db_sessionmaker() as db:
        saved = await db.get(ConversationExchange, (row.id, 1))
        assert "provider snippet" not in decrypt_token(saved.payload_enc)


async def test_retention_dry_run_and_apply_cascade_archive_without_deleting_live_chat(
    configured, db_sessionmaker
):
    expired = await archived_chat(db_sessionmaker, [exchange(1, "Expired history")])
    live = await archived_chat(db_sessionmaker, [exchange(1, "Live history")])
    async with db_sessionmaker.begin() as db:
        row = await db.get(Conversation, expired.request.conversation_id)
        row.expires_at = datetime.now(UTC) - timedelta(days=8)
    result = await retention.cleanup(db_sessionmaker)
    assert result["counts"]["expired_conversations"] == 1
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(ConversationExchange)) == 2
    await retention.cleanup(db_sessionmaker, apply=True)
    async with db_sessionmaker() as db:
        assert await db.get(Conversation, expired.request.conversation_id) is None
        assert await db.get(ConversationExchange, (expired.request.conversation_id, 1)) is None
        assert await db.get(ConversationExchange, (live.request.conversation_id, 1)) is not None


def test_recent_context_budget_preserves_original_user_details():
    original = [exchange(i, "Q" * 4000, assistant="A" * 10_000) for i in range(1, 13)]
    view = memory.recent(original)
    assert len(json.dumps(view, ensure_ascii=False)) <= memory.RECENT_CHARS
    assert len(view) < len(original)
    assert all(e["user"] == "Q" * 4000 for e in view)
    assert len(original[0]["assistant"]) == 10_000


def test_compacted_legacy_draft_survives_history_window_eviction():
    draft = {
        "draft_id": "retained-draft",
        "recipient": "Alex",
        "subject": "Project note",
        "body": "Keep this exact generated text.",
        "unresolved_fields": [],
    }
    state = {
        "history": [exchange(1, "Draft a note", email_draft=draft)]
        + [exchange(i, "An unrelated follow-up") for i in range(2, 14)],
        "email_draft_goal": {"status": "drafted", "draft_id": draft["draft_id"]},
        "receipts": [],
    }
    store.compact(state)
    assert all(not item.get("email_draft") for item in state["history"])
    assert email_draft.current_text_draft(state) == draft


async def test_recall_skips_unversioned_legacy_turn_without_inventing_identity(
    configured, db_sessionmaker
):
    runtime = await archived_chat(db_sessionmaker, [exchange(1, "The known detail is indigo")])
    async with db_sessionmaker.begin() as db:
        chat = await db.get(Conversation, runtime.request.conversation_id)
        state = store.decode(chat)
        state["history"].insert(0, {"user": "An older unversioned note", "assistant": "Understood"})
        chat.state_enc = store.encode(state)
    recalled = await memory.recall(runtime, RecallConversation())
    assert [item["version"] for item in recalled["exchanges"]] == [1]
    assert recalled["exchanges"][0]["user"] == "The known detail is indigo"
