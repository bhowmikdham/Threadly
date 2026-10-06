"""Answer-first Calendar availability with synthetic evidence and explicit civil dates."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.calendar import conversation_tools as tools
from app.schemas.calendar import CalendarCoverage, FreeBusyOut, Preferences
from app.schemas.calendar_tools import CheckTimeAvailability

ANCHOR = datetime(2026, 10, 6, 12, 53, tzinfo=UTC)  # 23:53 Melbourne, 6 October.
START = datetime(2026, 10, 7, 6, tzinfo=UTC)  # 17:00 Melbourne, 7 October.


@pytest.fixture
def availability_provider(monkeypatch):
    prefs = Preferences(
        timezone="Australia/Melbourne",
        calendar_ids=["synthetic"],
        default_duration_minutes=30,
        working_periods=[{"weekday": 0, "start_minute": 540, "end_minute": 1020}],
        buffer_before_minutes=0,
        buffer_after_minutes=0,
        minimum_notice_minutes=0,
    )
    state = SimpleNamespace(
        busy=[], partial=False, stale=False, failure=False, calls=0, checked_at=ANCHOR
    )

    async def preferences(owner):
        return SimpleNamespace(preferences=prefs, version=1, account_version=1)

    async def freebusy(owner, request):
        state.calls += 1
        if state.failure:
            raise ApiError(503, "calendar_unavailable", "private provider failure")
        calendars = [CalendarCoverage(calendar_id="synthetic", status="known", busy=state.busy)]
        if state.partial:
            calendars.append(
                CalendarCoverage(
                    calendar_id="unavailable", status="unknown", reason="missing", busy=[]
                )
            )
        return FreeBusyOut(
            id="synthetic-evidence",
            preferences_version=1,
            account_version=1,
            policy_version="calendar-read-1.0.0",
            checked_at=state.checked_at,
            expires_at=state.checked_at + timedelta(minutes=-1 if state.stale else 5),
            start=request.start,
            end=request.end,
            coverage="unknown" if state.partial else "complete",
            calendars=calendars,
        )

    monkeypatch.setattr(tools.service, "get_preferences", preferences)
    monkeypatch.setattr(tools.service, "query_freebusy", freebusy)
    return state


async def check(*, subject="self", phrase="", anchor=ANCHOR):
    args = CheckTimeAvailability(
        subject=subject,
        date={"kind": "relative", "offset_days": 1},
        date_source="tomorrow",
        at_time="17:00",
        at_time_source="5pm",
        duration_phrase=phrase,
    )
    return await tools.execute(
        1,
        "check_time_availability",
        args,
        "am I free at 5pm tomorrow" + (f" for {phrase}" if phrase else ""),
        anchor=anchor,
    )


@pytest.mark.parametrize("busy", [False, True])
async def test_exact_reported_question_answer_and_card_details(availability_provider, busy):
    if busy:
        availability_provider.busy = [{"start": START, "end": START + timedelta(minutes=30)}]
    result = await check()
    answer = "No, you're busy" if busy else "Yes, you're free"
    assert result["text"] == (
        f"{answer} tomorrow at 5 pm. "
        "I checked your saved default 30-minute slot on your selected calendars."
    )
    card = result["calendar_tools"]
    assert card["availability"] == ("busy" if busy else "free")
    assert card["scope"] == "selected_calendars" and card["complete"]
    assert card["date"] == "2026-10-07" and card["timezone"] == "Australia/Melbourne"
    assert card["start"] == START.isoformat() and card["duration_source"] == "saved_default"
    assert card["duration_minutes"] == 30
    assert "Other people" not in result["text"] and "Busy:" not in result["text"]
    assert "cricket" not in str(result)  # Busy-only evidence cannot supply event titles.


@pytest.mark.parametrize("busy", [False, True])
async def test_partial_checks_never_claim_free_and_keep_known_busy(availability_provider, busy):
    availability_provider.partial = True
    if busy:
        availability_provider.busy = [{"start": START, "end": START + timedelta(minutes=30)}]
    result = await check()
    assert result["error_code"] == "calendar_coverage_incomplete"
    assert "coverage is incomplete" in result["text"]
    assert "Yes, you're free" not in result["text"]
    assert result["calendar_tools"]["availability"] == ("busy" if busy else "unknown")
    assert not result["calendar_tools"]["complete"]


async def test_later_overlap_does_not_claim_busy_at_start(availability_provider):
    availability_provider.busy = [
        {"start": START + timedelta(minutes=20), "end": START + timedelta(minutes=60)}
    ]
    result = await check(phrase="45 minutes")
    assert result["text"].startswith("No, that 45-minute slot tomorrow at 5 pm overlaps busy time.")
    card = result["calendar_tools"]
    assert card["duration_source"] == "requested"
    assert card["busy_periods"][0]["end"] == (START + timedelta(minutes=45)).isoformat()


@pytest.mark.parametrize("failure", ["stale", "failure"])
async def test_stale_or_failed_checks_never_return_free_card(availability_provider, failure):
    setattr(availability_provider, failure, True)
    result = await check()
    assert result["error_code"] in {"calendar_evidence_expired", "calendar_unavailable"}
    assert "calendar_tools" not in result and "free" not in result["text"]
    assert "private provider failure" not in result["text"]


async def test_other_person_scope_keeps_limitation_without_self_read(availability_provider):
    result = await check(subject="other")
    assert "don't have access" in result["text"]
    assert availability_provider.calls == 0 and "calendar_tools" not in result


async def test_relative_label_uses_checked_local_day_not_utc_day(availability_provider):
    # 00:05 Melbourne on 7 October, still 6 October UTC.
    anchor = datetime(2026, 10, 6, 13, 5, tzinfo=UTC)
    availability_provider.checked_at = anchor
    result = await check(anchor=anchor)
    assert "tomorrow at 5 pm" in result["text"]
    assert result["calendar_tools"]["date"] == "2026-10-08"
