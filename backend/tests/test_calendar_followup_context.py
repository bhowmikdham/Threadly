"""Real Calendar handlers, scripted semantic decisions and fresh synthetic reads."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.calendar import service
from app.conversation import calendar_context, engine
from app.conversation.runtime import Runtime
from app.schemas.calendar import CalendarCoverage, FreeBusyOut, Preferences
from app.schemas.conversation import CheckDayAvailability, RetryCalendarRead
from tests.test_conversation import Model, tool

ANCHOR = datetime(2026, 10, 5, 6, 55, tzinfo=UTC)
QUESTION = "check if i am free tmrw?"
DATE = {"subject": "self", "date": {"kind": "relative", "offset_days": 1}, "date_source": "tmrw"}


def runtime(state, instruction, *, anchor=ANCHOR):
    state["calendar_read_anchor"] = anchor.isoformat()
    request = SimpleNamespace(instruction=instruction, request_id=str(uuid4()))
    return Runtime(42, request, state, None)


@pytest.fixture
def provider(monkeypatch):
    state = {"coverage": "unknown", "calls": [], "timezone": "Australia/Melbourne"}

    async def prefs(owner):
        assert owner == 42
        return SimpleNamespace(
            version=state.get("version", 1),
            account_version=2,
            preferences=Preferences(
                timezone=state["timezone"],
                calendar_ids=["selected"],
                working_periods=[
                    {"weekday": n, "start_minute": 540, "end_minute": 1020} for n in range(5)
                ],
                buffer_before_minutes=0,
                buffer_after_minutes=0,
                minimum_notice_minutes=0,
                default_duration_minutes=30,
            ),
        )

    async def freebusy(owner, body):
        state["calls"].append((owner, body))
        if state.get("error"):
            raise ApiError(409, state["error"], "Review settings")
        known = state["coverage"] == "complete"
        return FreeBusyOut(
            id=str(uuid4()),
            preferences_version=body.expected_preferences_version,
            account_version=2,
            policy_version="calendar-read-1.0.0",
            start=body.start,
            end=body.end,
            checked_at=state.get("checked_at", ANCHOR),
            expires_at=state.get("checked_at", ANCHOR) + timedelta(minutes=5),
            coverage=state["coverage"],
            calendars=[
                CalendarCoverage(
                    calendar_id="selected",
                    display_name="PROVIDER-ONLY-HOLIDAY",
                    status="known" if known else "unknown",
                    reason=None if known else "provider_error",
                    busy=[],
                )
            ],
        )

    monkeypatch.setattr(service, "get_preferences", prefs)
    monkeypatch.setattr(service, "query_freebusy", freebusy)
    return state


@pytest.mark.parametrize("followup", ["check now", "try again", "I've fixed it, check again"])
async def test_reported_conversation_rechecks_same_day_with_new_preferences(provider, followup):
    state = {"history": [], "refs": {}}
    first = await engine.run(
        {}, runtime(state, QUESTION), Model(tool("check_day_availability", **DATE))
    )
    assert first["error_code"] == "calendar_coverage_incomplete"
    assert first["calendar_availability"]["date"] == "2026-10-06"
    assert "PROVIDER-ONLY-HOLIDAY" not in json.dumps(state)
    assert "evidence_id" not in json.dumps(state)
    provider.update(coverage="complete", version=2)
    second = await engine.run(
        {},
        runtime(state, followup, anchor=ANCHOR + timedelta(minutes=3)),
        Model(tool("retry_calendar_read")),
    )
    assert second["calendar_availability"]["date"] == "2026-10-06"
    assert second["calendar_availability"]["coverage"] == "complete"
    assert "error_code" not in second
    assert len(provider["calls"]) == 2
    assert provider["calls"][-1][1].expected_preferences_version == 2
    assert provider["calls"][0][1].start == provider["calls"][1][1].start


async def test_retries_after_midnight_preserve_date_but_clip_to_fresh_now(provider):
    state = {"history": [], "refs": {}}
    await runtime(state, QUESTION).call("check_day_availability", CheckDayAvailability(**DATE))
    provider["coverage"] = "complete"
    for now in [datetime(2026, 10, 5, 15, tzinfo=UTC), datetime(2026, 10, 5, 16, tzinfo=UTC)]:
        result = await runtime(state, "check now", anchor=now).call(
            "retry_calendar_read", RetryCalendarRead()
        )
        assert result["calendar_availability"]["date"] == "2026-10-06"
        assert provider["calls"][-1][1].start == now
    result = await runtime(state, "try again", anchor=datetime(2026, 10, 6, 15, tzinfo=UTC)).call(
        "retry_calendar_read", RetryCalendarRead()
    )
    assert result["kind"] == "clarification" and "already passed" in result["text"]
    assert len(provider["calls"]) == 3


async def test_day_remains_same_when_timezone_changes(provider):
    state = {"history": [], "refs": {}}
    await runtime(state, QUESTION).call("check_day_availability", CheckDayAvailability(**DATE))
    provider["timezone"] = "America/Los_Angeles"
    result = await runtime(state, "check now").call("retry_calendar_read", RetryCalendarRead())
    assert result["calendar_availability"]["date"] == "2026-10-06"
    assert result["calendar_availability"]["timezone"] == "America/Los_Angeles"


async def test_failed_connection_retains_request_until_repaired(provider):
    state = {"history": [], "refs": {}}
    provider["error"] = "calendar_preferences_stale"
    first = await runtime(state, QUESTION).call(
        "check_day_availability", CheckDayAvailability(**DATE)
    )
    assert first["error_code"] == "calendar_preferences_stale"
    provider.pop("error")
    second = await runtime(state, "check now").call("retry_calendar_read", RetryCalendarRead())
    assert second["calendar_availability"]["date"] == "2026-10-06"


async def test_new_date_replaces_request_for_subsequent_retry(provider):
    state = {"history": [], "refs": {}}
    await runtime(state, QUESTION).call("check_day_availability", CheckDayAvailability(**DATE))
    friday = {"subject": "self", "date": {"kind": "weekday", "weekday": 4}, "date_source": "Friday"}
    await runtime(state, "What about Friday?").call(
        "check_day_availability", CheckDayAvailability(**friday)
    )
    result = await runtime(state, "check again").call("retry_calendar_read", RetryCalendarRead())
    assert result["calendar_availability"]["date"] == "2026-10-09"


async def test_retry_cannot_discard_new_constraint_or_cancellation(provider):
    state = {"history": [], "refs": {}}
    await runtime(state, QUESTION).call("check_day_availability", CheckDayAvailability(**DATE))
    for instruction in ["check Friday instead", "check now from 2 pm to 4 pm", "check and book it"]:
        with pytest.raises(ValueError):
            await runtime(state, instruction).call("retry_calendar_read", RetryCalendarRead())
    cancelled = await runtime(state, "don't check again").call(
        "retry_calendar_read", RetryCalendarRead()
    )
    assert "won't" in cancelled["text"]
    assert len(provider["calls"]) == 1


async def test_retry_preserves_clock_window_and_duration(provider):
    from app.schemas.calendar_tools import FindFreeTimes

    state = {"history": [], "refs": {}}
    args = FindFreeTimes(
        **DATE,
        start_time="14:00",
        end_time="16:00",
        start_time_source="2 pm",
        end_time_source="4 pm",
        duration_phrase="30 minutes",
    )
    await runtime(state, "Find slots tmrw from 2 pm to 4 pm for 30 minutes").call(
        "find_free_times", args
    )
    result = await runtime(state, "check now").call("retry_calendar_read", RetryCalendarRead())
    assert "30-minute" in result["text"]
    assert provider["calls"][0][1].start == provider["calls"][1][1].start
    assert provider["calls"][0][1].end == provider["calls"][1][1].end
    assert len(provider["calls"]) == 2


async def test_no_previous_request_needs_clarification(provider):
    result = await runtime({"history": [], "refs": {}}, "check now").call(
        "retry_calendar_read", RetryCalendarRead()
    )
    assert result["kind"] == "clarification"
    assert provider["calls"] == []


def legacy_state(*, lower=ANCHOR, gap=False):
    original = {
        "user": QUESTION,
        "assistant": "PROVIDER-ONLY-HOLIDAY",
        "kind": "message",
        "source": "calendar_availability",
        "request_id": "old",
    }
    failed = {
        "user": "check now",
        "assistant": "Which date?",
        "kind": "clarification",
        "request_id": "bad",
    }
    state = {
        "history": [original, failed],
        "refs": {},
        "calendar_read_anchor": ANCHOR.isoformat(),
        "receipts": [
            {"request_id": "bad", "response": {"trace": [{"tool": "check_day_availability"}]}}
        ],
    }
    if gap:
        state["history"].insert(
            1, {"user": "Read my email", "kind": "message", "request_id": "mail"}
        )
    previous = calendar_context.legacy_context(state, lower)
    if previous:
        state[calendar_context.KEY] = previous
    return state


async def test_existing_reported_chat_recovers_user_request_without_provider_prose(provider):
    state = legacy_state()
    view = calendar_context.model_context(state)
    assert view["user_instruction"] == QUESTION
    assert "PROVIDER-ONLY-HOLIDAY" not in str(view)
    result = await engine.run(
        {},
        runtime(state, "check now"),
        Model(tool("retry_calendar_read"), tool("check_day_availability", **DATE)),
    )
    assert result["calendar_availability"]["date"] == "2026-10-06"
    assert len(provider["calls"]) == 1
    assert state[calendar_context.KEY]["arguments"] is not None


async def test_legacy_date_not_guessed_across_unknown_day_boundary(provider):
    state = legacy_state(lower=ANCHOR - timedelta(days=1))
    result = await engine.run(
        {},
        runtime(state, "check now"),
        Model(tool("retry_calendar_read"), tool("check_day_availability", **DATE)),
    )
    assert result["kind"] == "clarification"
    assert provider["calls"] == []


def test_legacy_recovery_does_not_cross_unrelated_topic():
    assert calendar_context.KEY not in legacy_state(gap=True)


async def test_model_can_quote_original_date_without_selecting_retry_tool(provider):
    state = {"history": [], "refs": {}}
    await runtime(state, QUESTION).call("check_day_availability", CheckDayAvailability(**DATE))
    result = await engine.run(
        {}, runtime(state, "check now"), Model(tool("check_day_availability", **DATE))
    )
    assert result["calendar_availability"]["date"] == "2026-10-06"
    assert len(provider["calls"]) == 2


async def test_single_start_retry_keeps_time_and_date_across_midnight(provider):
    from app.schemas.calendar_tools import CheckTimeAvailability

    state = {"history": [], "refs": {}}
    args = CheckTimeAvailability(**DATE, at_time="14:00", at_time_source="2 pm")
    await runtime(state, "am i free at 2 pm tmrw?").call("check_time_availability", args)
    first = provider["calls"][-1][1]
    provider.update(coverage="complete", checked_at=datetime(2026, 10, 5, 15, tzinfo=UTC))
    result = await runtime(state, "check now", anchor=datetime(2026, 10, 5, 15, tzinfo=UTC)).call(
        "retry_calendar_read", RetryCalendarRead()
    )
    assert result["calendar_tools"]["date"] == "2026-10-06"
    assert result["calendar_tools"]["duration_minutes"] == 30
    assert provider["calls"][-1][1].start == first.start
    assert provider["calls"][-1][1].end == first.end
