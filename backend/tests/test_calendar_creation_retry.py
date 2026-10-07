"""Exact reported title follow-up across a mocked Bedrock throttle; no live calls."""

# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.calendar import conversation_guard, event_choices, event_creation, event_draft, intent
from app.conversation import service, store
from app.db.models import ActionJob, AssistantAction, Conversation
from app.model_client.conversation import ConversationProviderError
from tests.test_calendar_creation import configured, ready, turn  # noqa: F401
from tests.test_calendar_service import setup  # noqa: F401
from tests.test_conversation import Model, tool

# Keep the reported Melbourne midnight/date relationship, but place the synthetic
# event in the future relative to PostgreSQL's real action-expiry clock.
REPLAY_ANCHOR = (datetime.now(UTC) + timedelta(days=1)).replace(
    hour=13, minute=53, second=0, microsecond=0
)
REPLAY_START = REPLAY_ANCHOR.astimezone(ZoneInfo("Australia/Melbourne")).replace(
    hour=18, minute=0, second=0, microsecond=0
)


def freeze_reported_clock(monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromtimestamp(REPLAY_ANCHOR.timestamp(), UTC)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(store, "datetime", Frozen)
    monkeypatch.setattr(event_creation, "datetime", Frozen)
    monkeypatch.setattr(event_draft, "datetime", Frozen)
    monkeypatch.setattr(event_choices, "datetime", Frozen)
    monkeypatch.setattr(conversation_guard, "datetime", Frozen)


async def test_ashu_title_followup_throttle_preserves_today_time_ask_and_exact_retry(
    configured, db_sessionmaker, monkeypatch
):
    freeze_reported_clock(monkeypatch)
    await ready(db_sessionmaker)
    first_request = turn("could you help me create an event at 6:00 p.m. today")
    first = await service.turn(
        1,
        first_request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", intent=None),
            tool("respond", kind="clarification", text="What would you like to call this event?"),
            tool(
                "prepare_calendar_event",
                title="",
                date={"kind": "relative", "offset_days": 0},
                date_source="today",
                time="18:00",
                time_source="6:00 p.m.",
            ),
        ),
    )
    assert first["kind"] == "clarification"
    assert first["trace"][1].get("reason") == "calendar_preparation_required"
    request = turn(
        "meeting with Ashu",
        conversation_id=first_request.conversation_id,
        expected_version=first["version"],
    )

    class Throttled:
        calls = 0

        async def decide(self, system, messages, tools):
            self.calls += 1
            raise ConversationProviderError("ThrottlingException", http_status=429)

    model = Throttled()
    with pytest.raises(ApiError) as error:
        await service.turn(1, request, factory=db_sessionmaker, model=model)
    assert error.value.code == "conversation_provider_unavailable"
    assert model.calls == 3
    async with db_sessionmaker() as db:
        row = await db.get(Conversation, request.conversation_id)
        assert row.pending_request_id == request.request_id
        assert row.calendar_approval_mode == "ask"
        assert row.lease_id is None
        retained = store.decode(row)["calendar_event_request"]["arguments"]
        assert retained["time"] == "18:00" and retained["date"]["offset_days"] == 0
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0

    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", continue_previous=True, title="meeting with Ashu"),
        ),
    )
    action = result["calendar_action"]
    assert action["state"] == "proposed"
    event = action["preview"]["event"]
    assert event["summary"] == "meeting with Ashu"
    assert event["attendees"] == []
    start = datetime.fromisoformat(event["start"]["dateTime"]).astimezone(
        ZoneInfo("Australia/Melbourne")
    )
    assert start == REPLAY_START
    replay = await service.turn(1, request, factory=db_sessionmaker, model=Model())
    assert replay["calendar_action"]["action_id"] == action["action_id"]
    async with db_sessionmaker() as db:
        saved = await db.get(AssistantAction, action["action_id"])
        assert saved.payload["send_updates"] == "none"
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    assert not any(c.url.path.endswith("/events") for c in configured[0])


async def test_complete_ashu_title_does_not_require_an_attendee_or_prose_fallback(
    configured, db_sessionmaker, monkeypatch
):
    freeze_reported_clock(monkeypatch)
    await ready(db_sessionmaker)
    request = turn("could you help me create an event at 6pm today named meeting with Ashu")
    result = await service.turn(
        1,
        request,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_calendar_event", intent=None),
            tool(
                "respond",
                kind="message",
                text=(
                    "I'm having trouble creating the event. Let me try a different approach—"
                    "could you confirm: should I add Ashu as an attendee (if so, what's their "
                    "email), and which calendar should this go on?"
                ),
            ),
            tool(
                "prepare_calendar_event",
                title="meeting with Ashu",
                date={"kind": "relative", "offset_days": 0},
                date_source="today",
                time="18:00",
                time_source="6pm",
            ),
        ),
    )
    assert result["trace"][1].get("reason") == "calendar_preparation_required"
    action = result["calendar_action"]
    assert action["state"] == "proposed"
    event = action["preview"]["event"]
    assert event["summary"] == "meeting with Ashu" and event["attendees"] == []
    assert datetime.fromisoformat(event["start"]["dateTime"]).astimezone(
        ZoneInfo("Australia/Melbourne")
    ) == REPLAY_START
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 1
        assert await db.scalar(select(func.count()).select_from(ActionJob)) == 0
    assert not any(c.url.path.endswith("/events") for c in configured[0])


@pytest.mark.parametrize(
    "prefix",
    [
        "help me",
        "please help me",
        "could you help me",
        "can you please help me",
        "would you please help me to",
    ],
)
def test_help_me_creation_is_routed_to_typed_preparation(prefix):
    instruction = f"{prefix} create an event at 6pm today named meeting with Ashu"
    intent.validate_creation(instruction, "meeting with Ashu")


@pytest.mark.parametrize(
    "instruction",
    [
        "Could you help me create a summary of this email: create an event at 6pm today",
        "Help me create a short email saying create an event at 6pm today",
        "Help me explain this request: create an event at 6pm today",
        "Could you help me create a summary\ncreate an event at 6pm today",
        "Don't help me create an event at 6pm today",
        'Explain "could you help me create an event at 6pm today"',
    ],
)
def test_help_me_prefix_does_not_promote_source_or_content_into_event_authority(instruction):
    with pytest.raises(intent.IntentNotAuthorized):
        intent.validate_creation(instruction, "meeting with Ashu")
