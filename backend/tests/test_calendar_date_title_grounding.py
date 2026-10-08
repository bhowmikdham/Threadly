"""Recorded Wednesday/title failures; scripted models and fake Google transport only."""
# ruff: noqa: F811

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.calendar import event_creation, event_draft
from app.conversation import service, store
from app.db.models import ActionApproval, ActionJob, AssistantAction, Conversation
from app.schemas.conversation import PrepareCalendarEvent
from tests.test_calendar_creation import configured, ready, run, turn  # noqa: F401
from tests.test_calendar_intent_source import ObservedModel
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import tool

ANCHOR = datetime(2026, 10, 8, 3, 59, 53, tzinfo=UTC)
TEXT = "create an event on the next wednesday at 3pm for a class"
FIELDS = {
    "title": "a class",
    "date": {"kind": "weekday", "weekday": 2, "week": "next"},
    "date_source": "next wednesday",
    "time": "15:00",
    "time_source": "3pm",
}
PREFS = {"timezone": "Australia/Melbourne", "default_duration_minutes": 30}


def resolve(monkeypatch, meaning, source, *, anchor=ANCHOR, **fields):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return anchor.astimezone(tz) if tz else anchor.replace(tzinfo=None)

    monkeypatch.setattr(event_creation, "datetime", Frozen)
    args = PrepareCalendarEvent(**(FIELDS | {"date": meaning, "date_source": source} | fields))
    return event_creation.resolve_times(args, source, PREFS, anchor)


@pytest.mark.parametrize(
    "meaning,source",
    [
        ({"kind": "absolute", "start": "2026-10-15"}, "next wednesday"),
        ({"kind": "weekday", "weekday": 3, "week": "next"}, "next wednesday"),
        ({"kind": "relative", "offset_days": 7}, "next wednesday"),
        ({"kind": "absolute", "start": "2026-10-21"}, "next wednesday"),
        ({"kind": "absolute", "start": "2026-10-15"}, "2026-10-14"),
        ({"kind": "absolute", "start": "2026-10-15"}, "Wednesday 2026-10-15"),
        ({"kind": "absolute", "start": "2026-10-21"}, "next Wednesday 2026-10-21"),
        ({"kind": "relative", "offset_days": 2}, "tomorrow"),
        ({"kind": "absolute", "start": "2026-10-11"}, "in 2 days"),
        ({"kind": "weekday", "weekday": 2, "week": "next"}, "this Wednesday"),
    ],
)
def test_conflicting_date_meaning_requires_model_repair(monkeypatch, meaning, source):
    with pytest.raises(event_draft.FieldRepairRequired) as caught:
        resolve(monkeypatch, meaning, source)
    assert caught.value.field == "date"
    assert caught.value.code == "calendar_field_interpretation_mismatch"


@pytest.mark.parametrize(
    "meaning,source,day",
    [
        (FIELDS["date"], "next wednesday", "2026-10-14"),
        (FIELDS["date"], "on the next Wednesday", "2026-10-14"),
        (FIELDS["date"], "Wednesday next week", "2026-10-14"),
        ({"kind": "absolute", "start": "2026-10-14"}, "next wednesday", "2026-10-14"),
        ({"kind": "absolute", "start": "2026-10-14"}, "Wednesday 2026-10-14", "2026-10-14"),
        ({"kind": "absolute", "start": "2026-10-15"}, "2026-10-15", "2026-10-15"),
        ({"kind": "absolute", "start": "2026-10-15"}, "15 October 2026", "2026-10-15"),
        ({"kind": "relative", "offset_days": 1}, "tmrw", "2026-10-09"),
        ({"kind": "relative", "offset_days": 2}, "the day after tomorrow", "2026-10-10"),
    ],
)
def test_valid_and_server_canonicalized_dates_keep_saved_duration(
    monkeypatch, meaning, source, day
):
    start, end = resolve(monkeypatch, meaning, source)
    assert start.astimezone(ZoneInfo(PREFS["timezone"])).date().isoformat() == day
    assert end - start == timedelta(minutes=30)


@pytest.mark.parametrize(
    "anchor,source,meaning,extra,expected",
    [
        (
            datetime(2026, 10, 8, 13, 5, tzinfo=UTC),
            "tomorrow",
            {"kind": "relative", "offset_days": 1},
            {},
            "2026-10-10T04:00:00+00:00",
        ),
        (
            datetime(2026, 10, 8, 13, 5, tzinfo=UTC),
            "tomorrow",
            {"kind": "relative", "offset_days": 1},
            {"timezone": "UTC", "timezone_source": "UTC"},
            "2026-10-09T15:00:00+00:00",
        ),
        (
            datetime(2026, 10, 3, 2, tzinfo=UTC),
            "tomorrow",
            {"kind": "relative", "offset_days": 1},
            {},
            "2026-10-04T04:00:00+00:00",
        ),
        (
            datetime(2027, 4, 3, 2, tzinfo=UTC),
            "tomorrow",
            {"kind": "relative", "offset_days": 1},
            {},
            "2027-04-04T05:00:00+00:00",
        ),
    ],
)
def test_date_consistency_uses_local_anchor_and_dst(
    monkeypatch, anchor, source, meaning, extra, expected
):
    start, end = resolve(monkeypatch, meaning, source, anchor=anchor, **extra)
    assert start.isoformat() == expected
    assert end - start == timedelta(minutes=30)


@pytest.mark.parametrize(
    "phrase,title",
    [
        ("a class", "Class"),
        ("an interview", "Interview"),
        ("a rehearsal with Kelly", "Rehearsal with Kelly"),
    ],
)
def test_unquoted_indefinite_article_can_be_removed_without_noun_whitelist(phrase, title):
    event_creation.source_fields(
        PrepareCalendarEvent(**(FIELDS | {"title": title})), TEXT.replace("a class", phrase)
    )


@pytest.mark.parametrize(
    "text,title",
    [
        (TEXT, "Unrelated"),
        (TEXT.replace("a class", "a class rehearsal"), "Class"),
        (TEXT.replace("a class", '"a class"'), "Class"),
        (TEXT.replace("a class", "“a class”"), "Class"),
        (TEXT.replace("a class", "'a class'"), "Class"),
        (TEXT.replace("a class", "A Christmas Carol"), "Christmas Carol"),
        (TEXT.replace("a class", "a Christmas Carol"), "Christmas Carol"),
        (TEXT.replace("a class", "The Office"), "Office"),
        ("Create an event called The Office next wednesday at 3pm", "Office"),
        ("Create an event named a class next wednesday at 3pm", "Class"),
        ('Create an event for "A Class" next wednesday at 3pm', "Class"),
        ('Create an event "The Office" next wednesday at 3pm', "Office"),
        ("Set title to The Office", "Office"),
    ],
)
def test_cleanup_cannot_change_literal_names_or_substantive_title_words(text, title):
    with pytest.raises((event_draft.IncompleteEventTitle, event_draft.FieldRepairRequired)):
        event_creation.source_fields(PrepareCalendarEvent(**(FIELDS | {"title": title})), text)


@pytest.mark.parametrize("title", ['"The Office"', '"a class"', "A Christmas Carol"])
def test_complete_literal_title_remains_accepted(title):
    event_creation.source_fields(
        PrepareCalendarEvent(**(FIELDS | {"title": title.strip('"')})),
        TEXT.replace("a class", title),
    )


def next_wednesday():
    today = datetime.now(UTC).astimezone(ZoneInfo(PREFS["timezone"])).date()
    return today + timedelta(days=2 - today.weekday() + 7)


async def test_recorded_request_repairs_date_then_previews_class_without_approval(
    configured, db_sessionmaker, db_client, auth_headers
):
    await ready(db_sessionmaker)
    wrong = (next_wednesday() + timedelta(days=1)).isoformat()
    fields = FIELDS | {"title": "Class"}
    model = ObservedModel(
        tool("prepare_calendar_event", **(fields | {"date": {"kind": "absolute", "start": wrong}})),
        tool("prepare_calendar_event", **fields),
    )
    result = await service.turn(1, turn(TEXT), factory=db_sessionmaker, model=model)
    assert result["trace"][0] == {
        "tool": "prepare_calendar_event",
        "status": "invalid",
        "reason": "calendar_field_interpretation_mismatch",
        "field": "date",
    }
    assert len(model.contexts) == 2
    feedback = model.messages[-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert "weekday" in feedback["message"]
    action = result["calendar_action"]
    event = action["preview"]["event"]
    assert event["summary"] == "Class" and action["state"] == "proposed"
    assert (
        datetime.fromisoformat(event["start"]["dateTime"])
        .astimezone(ZoneInfo(PREFS["timezone"]))
        .date()
        == next_wednesday()
    )
    assert datetime.fromisoformat(event["end"]["dateTime"]) - datetime.fromisoformat(
        event["start"]["dateTime"]
    ) == timedelta(minutes=30)
    assert (
        db_client.get(
            f"/assistant/calendar-actions/{action['action_id']}", headers=auth_headers(2)
        ).status_code
        == 404
    )
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionApproval)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    assert not any(c.url.path.endswith("/events") for c in configured[0])


@pytest.mark.parametrize("explicit", [False, True])
async def test_invalid_date_revision_preserves_old_candidate_until_repaired(
    configured, db_sessionmaker, explicit
):
    await ready(db_sessionmaker)
    first = await run(db_sessionmaker, turn(TEXT), FIELDS)
    old = first["calendar_action"]
    source = (next_wednesday() + timedelta(days=2)).isoformat() if explicit else "Friday next week"
    text = "Move it to " + source
    good = (
        {"kind": "absolute", "start": source}
        if explicit
        else {"kind": "weekday", "weekday": 4, "week": "next"}
    )
    bad = {"kind": "weekday", "weekday": 3, "week": "next"}

    def decision(value):
        return tool(
            "prepare_calendar_event",
            continue_previous=True,
            intent={"operation": "revise", "source": text},
            changes=[
                {
                    "field": "date",
                    "operation": "replace",
                    "source": source,
                    "value": value,
                }
            ],
        )

    class CheckUnchanged(ObservedModel):
        async def decide(self, system, messages, tools):
            async with db_sessionmaker() as db:
                assert (await db.get(AssistantAction, old["action_id"])).state == "proposed"
                state = store.decode(await db.get(Conversation, first["conversation_id"]))
                assert state["calendar_event_request"]["action_id"] == old["action_id"]
            return await super().decide(system, messages, tools)

    model = CheckUnchanged(decision(bad), decision(good))
    result = await service.turn(
        1,
        turn(text, conversation_id=first["conversation_id"], expected_version=first["version"]),
        factory=db_sessionmaker,
        model=model,
    )
    assert result["trace"][0]["reason"] == "calendar_field_interpretation_mismatch"
    assert len(model.contexts) == 2
    new = result["calendar_action"]
    assert new["action_id"] != old["action_id"] and new["state"] == "proposed"
    assert (
        datetime.fromisoformat(new["preview"]["event"]["start"]["dateTime"])
        .astimezone(ZoneInfo(PREFS["timezone"]))
        .weekday()
        == 4
    )
    async with db_sessionmaker() as db:
        assert (await db.get(AssistantAction, old["action_id"])).state == "superseded"
        assert await db.get(ActionJob, new["action_id"]) is None
    assert not any(c.url.path.endswith("/events") for c in configured[0])


async def test_unrepaired_date_never_creates_a_preview_or_retains_the_bad_field(
    configured, db_sessionmaker
):
    from app.conversation.engine import MAX_CALLS

    await ready(db_sessionmaker)
    wrong = (next_wednesday() + timedelta(days=1)).isoformat()
    request = turn(TEXT)
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=ObservedModel(
            *[
                tool(
                    "prepare_calendar_event",
                    **(FIELDS | {"date": {"kind": "absolute", "start": wrong}}),
                )
                for _ in range(MAX_CALLS)
            ]
        ),
    )
    assert result["error_code"] == "calendar_event_not_prepared"
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
        state = store.decode(await db.get(Conversation, request.conversation_id))
        assert state["calendar_event_request"]["arguments"]["date"] is None
        assert state["calendar_event_request"]["arguments"]["time"] == "15:00"
    assert configured[0] == []


@pytest.mark.parametrize("legacy", [False, True])
async def test_timezone_correction_preserves_original_local_date_anchor(
    configured, db_sessionmaker, monkeypatch, legacy
):
    await ready(db_sessionmaker)
    first = await run(db_sessionmaker, turn(TEXT), FIELDS)

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return ANCHOR.astimezone(tz) if tz else ANCHOR.replace(tzinfo=None)

    monkeypatch.setattr(event_creation, "datetime", Frozen)
    # Model the pre/post-1.2.1 retained shape at a clock when Melbourne and UTC
    # are on different days. Only the owned old preview supplies a legacy zone.
    original = PrepareCalendarEvent(
        **(
            FIELDS
            | {"date": {"kind": "absolute", "start": "2026-10-10"}, "date_source": "tomorrow"}
        )
    )
    pending = {
        "anchor": "2026-10-08T13:05:00+00:00",
        "arguments": original.model_dump(mode="json"),
        "action_id": first["calendar_action_id"],
        "date_anchor_timezone": None if legacy else "Australia/Melbourne",
    }
    corrected = original.model_copy(update={"timezone": "UTC", "timezone_source": "UTC"})
    saved = deepcopy(pending)
    saved["arguments"] = corrected.model_dump(mode="json")
    runtime = SimpleNamespace(owner=1, factory=db_sessionmaker)
    await event_creation.validate_date(runtime, corrected, saved, pending)
    assert saved["date_anchor_timezone"] == "Australia/Melbourne"
    start, _ = event_creation.resolve_times(
        corrected,
        "tomorrow",
        PREFS,
        datetime.fromisoformat(pending["anchor"]),
        saved["date_anchor_timezone"],
    )
    assert start.isoformat() == "2026-10-10T15:00:00+00:00"
    if legacy:
        # Another account cannot use that preview as provenance for its date.
        saved["date_anchor_timezone"] = None
        with pytest.raises(event_draft.FieldRepairRequired):
            await event_creation.validate_date(
                SimpleNamespace(owner=2, factory=db_sessionmaker), corrected, saved, pending
            )
