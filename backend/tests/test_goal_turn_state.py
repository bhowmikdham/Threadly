"""Validated turn state must not be replaced by an older registry snapshot."""
# ruff: noqa: F401, F811

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.errors import ApiError
from app.auth.crypto import decrypt_token
from app.calendar.event_draft import FieldRepairRequired
from app.conversation import email_draft, goals, store
from app.db.models import AssistantAction, Conversation, ConversationGoal
from app.schemas.conversation import PrepareCalendarEvent, SelectConversationGoal, StartEmailDraft
from tests.test_chat_context_live_regressions import calendar_preview, prepared
from tests.test_chat_context_sources_goals import _runtime
from tests.test_conversation import configured, request
from tests.test_email_draft_clarification import DRAFT, runtime
from tests.test_on_demand_gmail import setup
from tools.replay_context5_retained_fields import test_same_turn_repair_retains_validated_purpose


async def owned_runtime(factory, previous, text="Continue this work"):
    req = request().model_copy(
        update={
            "conversation_id": previous["conversation_id"],
            "instruction": text,
            "expected_version": previous["version"],
        }
    )
    async with factory() as db:
        state = store.decode(await db.get(Conversation, req.conversation_id))
    return await _runtime(factory, req, state)


@pytest.mark.parametrize("kind", list(goals.KEYS))
async def test_repeated_selection_and_switch_preserve_validated_state(
    prepared, db_sessionmaker, kind
):
    r = await owned_runtime(db_sessionmaker, prepared[0]["previous"])
    key = goals.KEYS[kind]
    first = {"goal_id": "first-" + kind, "instruction": "First", "status": "clarification"}
    second = {"goal_id": "second-" + kind, "instruction": "Second", "status": "clarification"}
    if kind == "calendar_event":
        for value in (first, second):
            value["expires_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    async with db_sessionmaker() as db:
        chat = await store.owned(db, r.owner, r.request.conversation_id)
        for value in (first, second):
            r.state[key] = deepcopy(value)
            await goals.persist(db, chat, r.state, 1)
            await db.flush()
        await db.commit()
    async with db_sessionmaker.begin() as db:
        _, r.state, r.lease, _ = await store.claim(db, r.owner, r.request)

    def choose(value):
        return SelectConversationGoal(goal_id=value["goal_id"], source=r.request.instruction)

    await r.call("select_conversation_goal", choose(first))
    # A tool's validated update, including an explicit clear. The selector must
    # retain the entire newest value, not union nonempty fields from old state.
    r.state[key].update(instruction="Validated correction", purpose="", revision=2)
    await r.call("select_conversation_goal", choose(first))
    assert r.state[key]["instruction"] == "Validated correction"
    await r.call("select_conversation_goal", choose(second))
    assert r.state[key]["instruction"] == "Second"
    await r.call("select_conversation_goal", choose(first))
    assert r.state[key]["instruction"] == "Validated correction"
    assert r.state[key]["purpose"] == ""
    # Finish focused on another goal: both updates must reach the same commit.
    await r.call("select_conversation_goal", choose(second))
    response = {"kind": "message", "text": "Work retained."}
    async with db_sessionmaker.begin() as db:
        with pytest.raises(ApiError, match="Reload this conversation"):
            await store.complete(db, r.owner, r.request, "lost-lease", r.state, response)
        row = await db.get(ConversationGoal, (r.request.conversation_id, first["goal_id"]))
        assert "revision" not in json.loads(decrypt_token(row.payload_enc))["value"]
    async with db_sessionmaker() as db:
        await store.complete(db, r.owner, r.request, r.lease, r.state, response)
        await db.commit()
        row = await db.get(ConversationGoal, (r.request.conversation_id, first["goal_id"]))
        assert json.loads(decrypt_token(row.payload_enc))["value"]["revision"] == 2
    assert not any(k.startswith("_goal") for k in store.compact(r.state))


async def test_switching_from_an_uncommitted_new_email_does_not_discard_it(
    prepared, db_sessionmaker
):
    text = "Draft another email to Casey to thank them for the map"
    r = await owned_runtime(db_sessionmaker, prepared[0]["previous"], text)
    async with db_sessionmaker.begin() as db:
        _, r.state, r.lease, _ = await store.claim(db, r.owner, r.request)
    alex_id = r.state[email_draft.KEY]["goal_id"]
    with pytest.raises(email_draft.EmailDraftInputError):
        await r.call(
            "start_email_draft",
            StartEmailDraft(
                request_source=text, recipient="Casey", purpose="thank them for the map"
            ),
        )
    await r.call("select_conversation_goal", SelectConversationGoal(goal_id=alex_id, source=text))
    async with db_sessionmaker.begin() as db:
        await store.checkpoint(
            db, r.owner, r.request, r.lease, r.state, {"kind": "message", "text": "Work retained."}
        )
        rows = (
            await db.scalars(
                select(ConversationGoal).where(
                    ConversationGoal.conversation_id == r.request.conversation_id
                )
            )
        ).all()
        values = [json.loads(decrypt_token(row.payload_enc))["value"] for row in rows]
        assert len(values) == 2
        casey = next(value for value in values if value["recipient"] == "Casey")
        assert casey["goal_id"] != alex_id
        assert casey["purpose"] == "thank them for the map"
        chat = await db.get(Conversation, r.request.conversation_id)
        assert goals.UPDATES not in store.decode(chat)


@pytest.mark.parametrize("violation", ["source", "owner", "chat", "kind", "closed", "db_closed"])
async def test_working_state_never_bypasses_selection_authority(
    prepared, db_sessionmaker, violation
):
    r = await owned_runtime(db_sessionmaker, prepared[0]["previous"])
    value = r.state[email_draft.KEY]
    args = SelectConversationGoal(goal_id=value["goal_id"], source=r.request.instruction)
    await r.call("select_conversation_goal", args)
    value = r.state[email_draft.KEY]
    value["purpose"] = "Validated in this turn"
    expected_kind = None
    if violation == "source":
        args = args.model_copy(update={"source": "A previous USER turn"})
    elif violation == "owner":
        r.owner = 999
    elif violation == "chat":
        r.request = r.request.model_copy(
            update={"conversation_id": prepared[2]["previous"]["conversation_id"]}
        )
    elif violation == "kind":
        expected_kind = "calendar_event"
    elif violation == "closed":
        goals.close(r.state, value)
        r.state.pop(email_draft.KEY)
    else:
        async with db_sessionmaker.begin() as db:
            row = await db.get(ConversationGoal, (r.request.conversation_id, args.goal_id))
            row.status = "closed"
    with pytest.raises((ValueError, ApiError)):
        await goals.select_goal(r, args, expected_kind=expected_kind)


async def test_new_email_generation_retry_preserves_its_own_validated_fields():
    text = "Draft an email to Alex to ask about the sapphire crate"
    r = runtime(text)
    with pytest.raises(email_draft.EmailDraftInputError, match="draft_text_required"):
        await r.call(
            "start_email_draft",
            StartEmailDraft(
                request_source=text, recipient="Alex", purpose="ask about the sapphire crate"
            ),
        )
    response = await r.call("start_email_draft", StartEmailDraft(request_source=text, draft=DRAFT))
    assert response["email_draft"]["recipient"] == "Alex"
    assert r.state[email_draft.KEY]["purpose"] == "ask about the sapphire crate"


async def test_validated_email_correction_survives_invalid_generated_text():
    from app.schemas.conversation import PrepareEmailDraft

    r = runtime("Draft an email to Alex to say hello")
    await r.call(
        "prepare_email_draft", PrepareEmailDraft(recipient="Alex", purpose="say hello", draft=DRAFT)
    )
    r = runtime("Actually to Priya to say thanks", r.state)
    with pytest.raises(email_draft.EmailDraftInputError):
        await r.call(
            "prepare_email_draft",
            PrepareEmailDraft(continue_previous=True, recipient="Priya", purpose="say thanks"),
        )
    assert r.state[email_draft.KEY]["recipient"] == "Priya"
    assert email_draft.current_text_draft(r.state) is None
    response = await r.call(
        "prepare_email_draft", PrepareEmailDraft(continue_previous=True, draft=DRAFT)
    )
    assert response["email_draft"]["recipient"] == "Priya"


async def test_reselection_hydrates_only_the_matching_compacted_draft(prepared, db_sessionmaker):
    from tests.test_chat_context_live_regressions import alex_and_casey

    previous, alex_id, _ = await alex_and_casey(db_sessionmaker, prepared)
    r = await owned_runtime(db_sessionmaker, previous)
    expected = email_draft.current_text_draft(r.state)
    r.state[email_draft.KEY].pop("draft", None)
    r.state["history"] = []
    args = SelectConversationGoal(goal_id=alex_id, source=r.request.instruction)
    result = await r.call("select_conversation_goal", args)
    assert result["email_draft"] == expected
    # A newer generated-draft identity must not hydrate old text from the row.
    r.state[email_draft.KEY].update(draft_id="newer-draft")
    r.state[email_draft.KEY].pop("draft", None)
    result = await r.call("select_conversation_goal", args)
    assert result["email_draft"] is None


async def test_new_calendar_field_retry_retains_its_own_validated_fields(prepared, db_sessionmaker):
    text = "Create Sapphire tomorrow at 7 pm"
    r = await owned_runtime(db_sessionmaker, prepared[0]["previous"], text)
    async with db_sessionmaker.begin() as db:
        _, r.state, r.lease, _ = await store.claim(db, r.owner, r.request)
    intent = {"operation": "create", "source": text}
    with pytest.raises(FieldRepairRequired):
        await r.call(
            "prepare_calendar_event",
            PrepareCalendarEvent(
                intent=intent,
                title="Sapphire",
                date={"kind": "relative", "offset_days": 1},
                date_source="tomorrow",
                time="07:00",
                time_source="7 pm",
            ),
        )
    result = await r.call(
        "prepare_calendar_event",
        PrepareCalendarEvent(intent=intent, time="19:00", time_source="7 pm"),
    )
    assert result["kind"] == "calendar_event", result
    assert r.state["calendar_event_request"]["arguments"]["title"] == "Sapphire"


async def test_calendar_clear_and_retired_candidate_survive_reselection(prepared, db_sessionmaker):
    previous = await calendar_preview(db_sessionmaker)
    text = "Clear the date"
    r = await owned_runtime(db_sessionmaker, previous, text)
    async with db_sessionmaker.begin() as db:
        _, r.state, r.lease, _ = await store.claim(db, r.owner, r.request)
    goal_id = r.state["calendar_event_request"]["goal_id"]
    result = await r.call(
        "prepare_calendar_event",
        PrepareCalendarEvent(
            continue_previous=True,
            intent={"operation": "revise", "source": text},
            changes=[{"field": "date", "operation": "clear", "source": text}],
        ),
    )
    assert result["kind"] == "clarification"
    await r.call("select_conversation_goal", SelectConversationGoal(goal_id=goal_id, source=text))
    pending = r.state["calendar_event_request"]
    assert pending["arguments"]["date"] is None
    assert "action_id" not in pending
    async with db_sessionmaker() as db:
        action = await db.get(AssistantAction, previous["calendar_action_id"])
        assert action.state == "superseded"
