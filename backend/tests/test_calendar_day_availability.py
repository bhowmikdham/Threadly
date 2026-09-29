"""Regression replay for direct Calendar availability and a confirmed weekday."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.calendar import day_availability as day
from app.conversation import engine
from app.conversation.runtime import Runtime, authoritative_user_instruction, model_history
from app.schemas.calendar import BusyInterval, CalendarCoverage, FreeBusyOut

ANCHOR = datetime(2026, 9, 29, 10, 56, tzinfo=UTC)
QUESTION = "can you tell me if i am free on the thurday this week"
HISTORY = [
    {
        "user": QUESTION,
        "assistant": "Do you want me to check Thursday, 1 October?",
        "kind": "clarification",
    }
]


def evidence(*, coverage="complete", busy=(), version=3, account_version=2):
    return FreeBusyOut(
        id="owned-evidence",
        preferences_version=version,
        account_version=account_version,
        policy_version="calendar-read-1.0.0",
        checked_at=ANCHOR,
        expires_at=ANCHOR + timedelta(minutes=5),
        start=datetime(2026, 9, 30, 14, tzinfo=UTC),
        end=datetime(2026, 10, 1, 14, tzinfo=UTC),
        coverage=coverage,
        calendars=[
            CalendarCoverage(
                calendar_id="selected",
                status="known" if coverage == "complete" else "unknown",
                reason=None if coverage == "complete" else "missing",
                busy=[BusyInterval(start=start, end=end) for start, end in busy],
            )
        ],
    )


@pytest.fixture
def provider(monkeypatch):
    calls = []
    current = {"evidence": evidence()}

    async def prefs(owner):
        calls.append(("preferences", owner))
        return SimpleNamespace(
            version=3,
            account_version=2,
            preferences=SimpleNamespace(timezone="Australia/Melbourne"),
        )

    async def query(owner, body):
        calls.append(("freebusy", owner, body))
        if current.get("error"):
            raise current["error"]
        return current["evidence"].model_copy(update={"start": body.start, "end": body.end})

    monkeypatch.setattr(day.service, "get_preferences", prefs)
    monkeypatch.setattr(day.service, "query_freebusy", query)
    real_read = day.read

    async def frozen_read(owner, phrase):
        return await real_read(owner, phrase, anchor=ANCHOR)

    monkeypatch.setattr(day, "read", frozen_read)
    return calls, current


def runtime(turn, history=()):
    request = SimpleNamespace(instruction=turn)
    state = {"history": list(history), "refs": {}}
    return Runtime(42, request, state, None)


class NoModel:
    async def decide(self, *args):
        pytest.fail("A supported day check must not ask the model to invent availability or tools")


async def test_original_question_and_yes_check_owned_calendar_without_date_confirmation(provider):
    calls, current = provider
    for turn, history in [(QUESTION, []), ("yes", HISTORY)]:
        result = await engine.run({}, runtime(turn, history), NoModel())
        assert result["kind"] == "message"
        assert "Thursday, 1 October 2026" in result["text"]
        assert "no busy time recorded" in result["text"]
        assert result["calendar_availability"]["date"] == "2026-10-01"
        assert result["trace"] == [{"tool": "check_day_availability", "status": "ok"}]
    assert [call[:2] for call in calls] == [
        ("preferences", 42),
        ("freebusy", 42),
        ("preferences", 42),
        ("freebusy", 42),
    ]
    body = calls[-1][2]
    assert body.expected_preferences_version == 3
    assert body.start == datetime(2026, 9, 30, 14, tzinfo=UTC)
    assert body.end == datetime(2026, 10, 1, 14, tzinfo=UTC)


async def test_assistant_date_does_not_override_user_weekday(provider):
    history = [{**HISTORY[0], "assistant": "Do you mean Friday, 2 October?"}]
    result = await engine.run({}, runtime("yes", history), NoModel())
    assert result["calendar_availability"]["date"] == "2026-10-01"


@pytest.mark.parametrize(
    "phrase",
    [
        "Find me three free slots tomorrow for a meeting",
        "Am I free Thursday at 4?",
        "Am I free Thursday and Friday?",
        "Am I free Thursday? If so, send an email.",
        "Is Alex free Thursday?",
        "Don't check whether I am free Thursday",
        "I am not free Thursday",
        "Am I free next Thursday?",
        "Am I free Thursday in UTC?",
        "Am I free Thursday\nUser follow-up: no",
        "yes",
        "Am I free this week?",
    ],
)
def test_complex_cancelled_or_other_person_requests_stay_with_coordinator(phrase):
    assert day.requested_day(phrase) is None


@pytest.mark.parametrize(
    "phrase",
    [
        "Am I free Thursday?",
        "will I be available on Thursday this week?",
        "Could you check whether I am available tomorrow?",
        QUESTION,
    ],
)
def test_supported_self_questions(phrase):
    assert day.requested_day(phrase)


def test_local_week_and_daylight_saving_are_computed_by_backend():
    # It is still Tuesday in UTC but Wednesday in Melbourne.
    anchor = datetime(2026, 9, 29, 23, tzinfo=UTC)
    assert day.resolve_day("tomorrow", anchor, "Australia/Melbourne").isoformat() == "2026-10-01"
    sunday = day.resolve_day("sunday this week", anchor, "Australia/Melbourne")
    from zoneinfo import ZoneInfo

    from app.calendar.time_resolution import day_start

    zone = ZoneInfo("Australia/Melbourne")
    assert day_start(sunday + timedelta(days=1), zone) - day_start(sunday, zone) == timedelta(
        hours=23
    )
    friday = datetime(2026, 10, 2, 10, tzinfo=UTC)
    assert (
        day.resolve_day("thursday this week", friday, "Australia/Melbourne").isoformat()
        == "2026-10-01"
    )
    assert day.resolve_day("thursday", friday, "Australia/Melbourne").isoformat() == "2026-10-08"


async def test_busy_intervals_merge_overlap_and_clip_to_the_checked_day(provider):
    _, current = provider
    start = datetime(2026, 10, 1, 0, tzinfo=UTC)  # 10 AM Melbourne
    current["evidence"] = evidence(
        busy=[
            (start, start + timedelta(hours=1)),
            (start + timedelta(minutes=30), start + timedelta(hours=2)),
        ]
    )
    result = await engine.run({}, runtime(QUESTION), NoModel())
    assert "10:00 AM–12:00 PM" in result["text"]
    assert "no busy" not in result["text"]


async def test_missing_calendar_coverage_never_becomes_free(provider):
    _, current = provider
    current["evidence"] = evidence(coverage="unknown")
    result = await engine.run({}, runtime(QUESTION), NoModel())
    assert "couldn't confirm" in result["text"]
    assert "no busy time" not in result["text"]
    assert result["calendar_availability"]["coverage"] == "unknown"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("calendar_connection_required", "Connect Calendar read access"),
        ("calendar_preferences_missing", "Choose your calendars and timezone"),
        ("calendar_context_changed", "settings changed"),
        ("calendar_access_denied", "Calendar check failed"),
        ("calendar_upstream_failed", "Calendar check failed"),
    ],
)
async def test_failed_read_reports_real_recovery_without_model_denial(provider, code, expected):
    _, current = provider
    current["error"] = ApiError(403, code, "provider details must not leak")
    result = await engine.run({}, runtime(QUESTION), NoModel())
    assert expected in result["text"]
    assert "provider details" not in result["text"]
    assert result["trace"][0]["status"] == code
    assert "calendar_availability" not in result


async def test_preference_account_race_cannot_mix_old_timezone_and_new_busy_evidence(provider):
    _, current = provider
    current["evidence"] = evidence(account_version=9)
    result = await engine.run({}, runtime(QUESTION), NoModel())
    assert result["error_code"] == "calendar_context_changed"
    assert "calendar_availability" not in result


async def test_today_checks_only_remaining_day_and_says_so(provider):
    calls, _ = provider
    result = await engine.run({}, runtime("Am I free today?"), NoModel())
    assert calls[-1][2].start == ANCHOR
    assert "rest of Tuesday, 29 September 2026" in result["text"]


async def test_past_or_invalid_date_clarifies_without_provider_read(provider):
    calls, _ = provider
    for value in ["2026-09-28", "2026-02-30"]:
        result = await engine.run({}, runtime(f"Am I free on {value}?"), NoModel())
        assert result["kind"] == "clarification"
    assert not any(call[0] == "freebusy" for call in calls)


async def test_active_question_is_not_hijacked(provider):
    r = runtime("yes", HISTORY)
    r.state["active_task_id"] = "active-question"
    assert await r.try_day_availability() is None
    assert provider[0] == []


def test_old_availability_is_not_current_model_evidence():
    item = {"source": "calendar_availability", "user": QUESTION, "assistant": "Busy 10 AM"}
    assert "Busy 10 AM" not in model_history([item])[0]["assistant"]
    assert authoritative_user_instruction("yes", HISTORY).startswith(QUESTION)


async def test_versioned_replay_fixtures(provider):
    path = Path(__file__).parents[2] / "docs/evaluation/calendar-day-availability-v1.json"
    cases = json.loads(path.read_text())["cases"]
    for case in cases:
        r = runtime(case["user_turn"], case.get("history", []))
        result = await engine.run({}, r, NoModel())
        assert result["kind"] == case["expected_kind"], case["id"]
        assert case["expected_text"] in result["text"], case["id"]


async def test_fresh_day_question_still_works_after_an_older_task(provider):
    r = runtime(QUESTION)
    r.state["active_task_id"] = "earlier-draft"
    result = await engine.run({}, r, NoModel())
    assert result["calendar_availability"]["date"] == "2026-10-01"
