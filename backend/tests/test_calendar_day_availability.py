"""Regression replay for direct Calendar availability and a confirmed weekday."""

import json
import re
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

    async def frozen_read(owner, phrase, **kwargs):
        return await real_read(
            owner, phrase, **{**kwargs, "anchor": kwargs.get("anchor") or ANCHOR}
        )

    monkeypatch.setattr(day, "read", frozen_read)
    return calls, current


def runtime(turn, history=()):
    request = SimpleNamespace(instruction=turn)
    state = {"history": list(history), "refs": {}, "calendar_read_anchor": ANCHOR.isoformat()}
    return Runtime(42, request, state, None)


class DayModel:
    """Scripted semantic decision; no claim that this fixture measures an LLM."""

    def __init__(self, phrase, source):
        self.phrase, self.source, self.calls = phrase, source, 0

    async def decide(self, prompt, messages, config):
        self.calls += 1
        return {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "day",
                        "name": "check_day_availability",
                        "input": {"date_phrase": self.phrase, "date_source": self.source},
                    }
                }
            ],
        }


async def semantic_day(r):
    # Translate historical fixtures into explicit scripted tool arguments. This
    # test helper is not runtime routing or a semantic model quality evaluation.
    instruction = r.authoritative_instruction()
    source = re.search(
        r"today|tomorrow|\d{4}-\d{2}-\d{2}|(?:this )?(?:thursday|thurday)(?: this week)?",
        instruction,
        re.I,
    )[0]
    model = DayModel(source.casefold().replace("thurday", "thursday"), source)
    result = await engine.run({}, r, model)
    assert model.calls == 1  # User text must reach the semantic model, never a regex fast path.
    return result


async def test_original_question_and_yes_check_owned_calendar_without_date_confirmation(provider):
    calls, current = provider
    for turn, history in [(QUESTION, []), ("yes", HISTORY)]:
        result = await semantic_day(runtime(turn, history))
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
    result = await semantic_day(runtime("yes", history))
    assert result["calendar_availability"]["date"] == "2026-10-01"


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
    result = await semantic_day(runtime(QUESTION))
    assert "10:00 AM–12:00 PM" in result["text"]
    assert "no busy" not in result["text"]


async def test_missing_calendar_coverage_never_becomes_free(provider):
    _, current = provider
    current["evidence"] = evidence(coverage="unknown")
    result = await semantic_day(runtime(QUESTION))
    assert "couldn't confirm" in result["text"]
    assert "no busy time" not in result["text"]
    assert result["calendar_availability"]["coverage"] == "unknown"


@pytest.mark.parametrize(
    "code,expected",
    [
        ("calendar_connection_required", "Connect Calendar read access"),
        ("calendar_preferences_missing", "Choose your calendars and timezone"),
        ("calendar_context_changed", "settings changed"),
        ("calendar_preferences_stale", "Review and save your calendars"),
        ("calendar_access_denied", "Reconnect Calendar"),
        ("calendar_upstream_failed", "Calendar check failed"),
    ],
)
async def test_failed_read_reports_real_recovery_without_model_denial(provider, code, expected):
    _, current = provider
    current["error"] = ApiError(403, code, "provider details must not leak")
    result = await semantic_day(runtime(QUESTION))
    assert expected in result["text"]
    assert "provider details" not in result["text"]
    assert result["trace"][0]["status"] == code
    assert "calendar_availability" not in result


async def test_preference_account_race_cannot_mix_old_timezone_and_new_busy_evidence(provider):
    _, current = provider
    current["evidence"] = evidence(account_version=9)
    result = await semantic_day(runtime(QUESTION))
    assert result["error_code"] == "calendar_context_changed"
    assert "calendar_availability" not in result


async def test_today_checks_only_remaining_day_and_says_so(provider):
    calls, _ = provider
    result = await semantic_day(runtime("Am I free today?"))
    assert calls[-1][2].start == ANCHOR
    assert "rest of Tuesday, 29 September 2026" in result["text"]


async def test_past_or_invalid_date_clarifies_without_provider_read(provider):
    calls, _ = provider
    for value in ["2026-09-28", "2026-02-30"]:
        result = await semantic_day(runtime(f"Am I free on {value}?"))
        assert result["kind"] == "clarification"
    assert not any(call[0] == "freebusy" for call in calls)


async def test_active_question_is_not_hijacked(provider):
    r = runtime("yes", HISTORY)
    r.state["active_task_id"] = "active-question"
    assert not hasattr(r, "try_day_availability")
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
        result = await semantic_day(r)
        assert result["kind"] == case["expected_kind"], case["id"]
        assert case["expected_text"] in result["text"], case["id"]


async def test_fresh_day_question_still_works_after_an_older_task(provider):
    r = runtime(QUESTION)
    r.state["active_task_id"] = "earlier-draft"
    result = await semantic_day(r)
    assert result["calendar_availability"]["date"] == "2026-10-01"


@pytest.mark.parametrize(
    "question",
    [
        "check if i am free on thursday",
        "check my availability for thursday this week",
        "check my availabiloty for thurday this week",
        "Can you check my availability on Thursday?",
        "please check whether I'm available Thursday",
    ],
)
async def test_reported_wording_bypasses_proposal_even_after_failed_proposal(provider, question):
    r = runtime(question)
    r.state["proposal_id"] = "old-failed-proposal"
    result = await semantic_day(r)
    assert result["kind"] == "message"
    assert result["calendar_availability"]["date"] == "2026-10-01"
    assert "proposal" not in result


@pytest.mark.parametrize(
    "question",
    [
        "Do I have anything on Thursday?",
        "How busy is my Thursday looking?",
        "Could you take a look at my free time this Thursday?",
    ],
)
async def test_semantic_paraphrases_use_backend_resolved_dates(provider, question):
    class ReadModel:
        async def decide(self, prompt, messages, config):
            assert "check_day_availability" in prompt
            assert any(t["toolSpec"]["name"] == "check_day_availability" for t in config["tools"])
            return {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "day-read",
                            "name": "check_day_availability",
                            "input": {"date_phrase": "Thursday", "date_source": "Thursday"},
                        }
                    }
                ],
            }

    result = await engine.run({}, runtime(question), ReadModel())
    assert result["kind"] == "message"
    assert result["calendar_availability"]["date"] == "2026-10-01"
    assert result["trace"] == [{"tool": "check_day_availability", "status": "ok"}]


async def test_unknown_holiday_calendar_does_not_hide_verified_busy_times(provider):
    _, current = provider
    checked = evidence(
        busy=[(datetime(2026, 10, 1, 0, tzinfo=UTC), datetime(2026, 10, 1, 1, tzinfo=UTC))]
    )
    checked.calendars.append(
        CalendarCoverage(
            calendar_id="private-provider-id",
            display_name="Holidays",
            status="unknown",
            reason="provider_error",
            busy=[],
        )
    )
    checked.coverage = "unknown"
    current["evidence"] = checked
    result = await semantic_day(runtime(QUESTION))
    assert "10:00 AM–11:00 AM" in result["text"]
    assert "Holidays couldn't be checked" in result["text"]
    assert "Review calendars" in result["text"]
    assert result["error_code"] == "calendar_coverage_incomplete"
    assert "no busy time recorded" not in result["text"]
    assert "private-provider-id" not in result["text"]
    assert result["calendar_availability"]["coverage"] == "unknown"


@pytest.mark.parametrize(
    "state", ["failed", "needs_clarification", "unsupported", "expired", "planning"]
)
def test_nonreviewable_proposals_do_not_claim_ready_for_review(state):
    from app.conversation.runtime import proposal_text

    assert "proposed work for you to review" not in proposal_text(state)
    assert "invalid_master_proposal" not in proposal_text(state)


async def test_versioned_semantic_replay_fixtures(provider):
    path = Path(__file__).parents[2] / "docs/evaluation/calendar-day-availability-v3.json"
    fixture = json.loads(path.read_text())
    assert fixture["release"] == day.POLICY
    for case in fixture["cases"]:

        class FixtureModel:
            async def decide(self, *args, case=case):
                return {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "replay",
                                "name": case["decision"]["tool"],
                                "input": case["decision"]["input"],
                            }
                        }
                    ],
                }

        result = await engine.run({}, runtime(case["user_turn"]), FixtureModel())
        assert result["release"] == engine.RELEASE  # Replay the historical cases on this release.
        assert result["calendar_availability"]["date"] == case["expected_date"]


def test_day_tool_rejects_untyped_dates_and_calendar_ids():
    from pydantic import ValidationError

    from app.schemas.conversation import CheckDayAvailability

    with pytest.raises(ValidationError):
        CheckDayAvailability.model_validate({"date": "2026-10-01", "calendar_id": "someone-else"})


@pytest.mark.parametrize(
    "text,source",
    [
        ("am i free tmrw ?", "tmrw"),
        ("am i free tommorrow", "tommorrow"),
        ("Got any breathing room the day following today?", "the day following today"),
    ],
)
async def test_semantic_dates_do_not_depend_on_a_spelling_dictionary(provider, text, source):
    calls, _ = provider
    model = DayModel("tomorrow", source)
    result = await engine.run({}, runtime(text), model)
    assert model.calls == 1
    assert result["kind"] == "message"
    assert result["calendar_availability"]["date"] == "2026-09-30"
    assert calls[-1][0:2] == ("freebusy", 42)
    assert calls[-1][2].start == datetime(2026, 9, 29, 14, tzinfo=UTC)


async def test_transient_preference_race_restarts_whole_read_once(provider, monkeypatch):
    calls, _ = provider
    original = day.service.query_freebusy
    attempts = 0

    async def query(owner, body):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise day.service.conflict()
        return await original(owner, body)

    monkeypatch.setattr(day.service, "query_freebusy", query)
    result = await semantic_day(runtime(QUESTION))
    assert "error_code" not in result
    assert attempts == 2
    assert len([call for call in calls if call[0] == "preferences"]) == 2


async def test_stale_saved_preferences_are_not_retried(provider):
    calls, current = provider
    current["error"] = ApiError(409, "calendar_preferences_stale", "Review")
    result = await semantic_day(runtime(QUESTION))
    assert result["error_code"] == "calendar_preferences_stale"
    assert len([call for call in calls if call[0] == "freebusy"]) == 1


async def test_continuous_settings_race_is_bounded(provider):
    calls, current = provider
    current["error"] = day.service.conflict()
    result = await semantic_day(runtime(QUESTION))
    assert result["error_code"] == "calendar_context_changed"
    assert len([call for call in calls if call[0] == "freebusy"]) == 2


async def test_retry_recomputes_day_with_new_preference_timezone(provider, monkeypatch):
    snapshots = [
        SimpleNamespace(
            version=3,
            account_version=2,
            preferences=SimpleNamespace(timezone="Australia/Melbourne"),
        ),
        SimpleNamespace(
            version=4, account_version=2, preferences=SimpleNamespace(timezone="America/New_York")
        ),
    ]
    reads = []

    async def prefs(owner):
        return snapshots.pop(0)

    async def query(owner, body):
        reads.append(body)
        if len(reads) == 1:
            raise day.service.conflict()
        return evidence(version=4).model_copy(update={"start": body.start, "end": body.end})

    monkeypatch.setattr(day.service, "get_preferences", prefs)
    monkeypatch.setattr(day.service, "query_freebusy", query)
    result = await semantic_day(runtime(QUESTION))
    assert result["calendar_availability"]["timezone"] == "America/New_York"
    assert reads[1].expected_preferences_version == 4
    assert reads[1].start == datetime(2026, 10, 1, 4, tzinfo=UTC)
    assert reads[1].end == datetime(2026, 10, 2, 4, tzinfo=UTC)
