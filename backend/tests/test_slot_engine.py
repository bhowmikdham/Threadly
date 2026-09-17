"""Independent interval, context, anchor and DST examples; no provider/model calls."""

from datetime import UTC, datetime, timedelta
from random import Random
from zoneinfo import ZoneInfo

import pytest

from app.calendar import availability as engine
from app.calendar import time_resolution
from app.calendar.slots import option, prepare
from app.schemas.calendar import CalendarCoverage, Preferences
from app.schemas.slots import SlotRequest

MON = datetime(2026, 9, 21, 9, tzinfo=UTC)


def prefs(**changes):
    return Preferences.model_validate(
        {
            "timezone": "UTC",
            "calendar_ids": ["work"],
            "working_periods": [
                {"weekday": i, "start_minute": 540, "end_minute": 1020} for i in range(5)
            ],
            "buffer_before_minutes": 0,
            "buffer_after_minutes": 0,
            "minimum_notice_minutes": 0,
            "default_duration_minutes": 30,
            **changes,
        }
    )


def coverage(intervals=(), *, status="known"):
    return [
        CalendarCoverage(
            calendar_id="work",
            status=status,
            busy=[{"start": lo, "end": hi} for lo, hi in intervals],
        )
    ]


def request(**changes):
    return SlotRequest.model_validate(
        {"request_id": "test", "expected_preferences_version": 1, "date": "tomorrow", **changes}
    )


def test_overlap_nesting_adjacency_buffers_and_fewer_than_three():
    busy = coverage(
        [
            (MON, MON + timedelta(minutes=30)),
            (MON + timedelta(minutes=15), MON + timedelta(minutes=20)),
            (MON + timedelta(minutes=30), MON + timedelta(minutes=60)),
        ]
    )
    result = engine.feasible_starts(
        MON, MON + timedelta(hours=2), 30, prefs(buffer_after_minutes=10), busy, MON
    )
    assert result == [MON + timedelta(minutes=75), MON + timedelta(minutes=90)]
    assert engine.select_options(result, 30, 3) == [MON + timedelta(minutes=75)]


def test_two_calendars_union_and_busy_outside_search_applies_buffers():
    calendars = coverage([(MON - timedelta(minutes=30), MON - timedelta(minutes=5))])
    calendars += [
        CalendarCoverage(
            calendar_id="personal",
            status="known",
            busy=[
                {
                    "start": MON + timedelta(hours=1, minutes=5),
                    "end": MON + timedelta(hours=2),
                }
            ],
        )
    ]
    result = engine.feasible_starts(
        MON,
        MON + timedelta(hours=1),
        30,
        prefs(buffer_before_minutes=10, buffer_after_minutes=20),
        calendars,
        MON - timedelta(hours=1),
    )
    assert result == [MON + timedelta(minutes=15)]


@pytest.mark.parametrize("case", ["all_day", "weekend", "unknown", "empty_coverage"])
def test_unavailable_or_unknown_never_yields_slots(case):
    start = MON if case != "weekend" else MON + timedelta(days=5)
    busy = (
        coverage([(start - timedelta(hours=9), start + timedelta(hours=15))])
        if case == "all_day"
        else coverage()
    )
    if case == "unknown":
        busy = coverage(status="unknown")
    if case == "empty_coverage":
        busy = []
    if case in {"unknown", "empty_coverage"}:
        with pytest.raises(ValueError):
            engine.feasible_starts(start, start + timedelta(hours=2), 30, prefs(), busy, start)
    else:
        assert (
            engine.feasible_starts(start, start + timedelta(hours=2), 30, prefs(), busy, start)
            == []
        )


def test_notice_duration_and_nonoverlapping_option_limit():
    result = engine.feasible_starts(
        MON, MON + timedelta(hours=2), 30, prefs(minimum_notice_minutes=45), coverage(), MON
    )
    assert engine.select_options(result, 30, 3) == [
        MON + timedelta(minutes=45),
        MON + timedelta(minutes=75),
    ]
    exact = MON + timedelta(minutes=7)
    assert engine.feasible_starts(
        exact, exact + timedelta(minutes=30), 30, prefs(), coverage(), MON, exact=True
    ) == [exact]


def test_split_overnight_periods_form_continuous_working_window():
    p = prefs(
        working_periods=[
            {"weekday": 0, "start_minute": 1380, "end_minute": 1440},
            {"weekday": 1, "start_minute": 0, "end_minute": 120},
        ]
    )
    start = MON.replace(hour=23)
    assert engine.working_windows(start, start + timedelta(hours=3), p) == [
        (start, start + timedelta(hours=3))
    ]
    result = engine.feasible_starts(start, start + timedelta(hours=3), 90, p, coverage(), MON)
    assert engine.select_options(result, 90, 3) == [start, start + timedelta(minutes=90)]


@pytest.mark.parametrize("day,hours", [("2026-04-05", 4), ("2026-10-04", 2)])
def test_melbourne_working_windows_include_both_folds_and_skip_gaps(day, hours):
    zone = ZoneInfo("Australia/Melbourne")
    date = datetime.fromisoformat(day).date()
    lo = time_resolution.wall_instants(date, 60, zone)[0]
    hi = time_resolution.wall_instants(date, 240, zone)[0]
    p = prefs(
        timezone=zone.key, working_periods=[{"weekday": 6, "start_minute": 60, "end_minute": 240}]
    )
    assert engine.working_windows(lo, hi, p) == [(lo, hi)]
    assert hi - lo == timedelta(hours=hours)
    starts = engine.feasible_starts(lo, hi, 30, p, coverage(), lo - timedelta(hours=1))
    assert all(engine.fits_working(at, at + timedelta(minutes=30), p) for at in starts)
    if hours == 4:
        assert sum(at.astimezone(zone).strftime("%H:%M") == "02:00" for at in starts) == 2
    else:
        assert all(at.astimezone(zone).hour != 2 for at in starts)


def test_exact_gap_fold_and_explicit_fold_selection():
    p = prefs(timezone="Australia/Melbourne")
    gap = time_resolution.resolve(request(date="2026-10-04", at_time="02:30"), p, MON)
    assert gap["reason"] == "nonexistent_local_time"
    folded = time_resolution.resolve(request(date="2026-04-05", at_time="02:30"), p, MON)
    assert folded["reason"] == "ambiguous_local_time"
    starts = [
        time_resolution.resolve(request(date="2026-04-05", at_time="02:30", fold=i), p, MON)[
            "start"
        ]
        for i in (0, 1)
    ]
    assert starts == ["2026-04-04T15:30:00+00:00", "2026-04-04T16:30:00+00:00"]


def test_bare_four_uses_context_and_working_hours_but_never_busy_state():
    p = prefs()
    inferred = time_resolution.resolve(request(date="2026-09-21", at_time="4"), p, MON)
    assert inferred["start"] == "2026-09-21T16:00:00+00:00"
    assert inferred["assumptions"][-1]["source"] == "saved_working_hours"
    night_and_day = prefs(working_periods=[{"weekday": 0, "start_minute": 0, "end_minute": 1440}])
    ambiguous = time_resolution.resolve(request(date="2026-09-21", at_time="4"), night_and_day, MON)
    assert ambiguous["reason"] == "ambiguous_meridiem"
    explicit_context = time_resolution.resolve(
        request(
            date="2026-09-21",
            at_time="4",
            time_context={
                "start_minute": 900,
                "end_minute": 1080,
            },
        ),
        night_and_day,
        MON,
    )
    assert explicit_context["start"] == inferred["start"]
    assert explicit_context["assumptions"][-1]["source"] == "explicit_time_context"


def test_relative_date_uses_saved_anchor_even_after_midnight_and_retry():
    anchor = datetime(2026, 9, 20, 23, 58, tzinfo=UTC)
    body = request()
    initial = time_resolution.resolve(body, prefs(), anchor)
    retry = prepare(body, prefs(), anchor, anchor + timedelta(minutes=10))
    assert initial["start"] == retry["start"] == "2026-09-21T00:00:00+00:00"
    assert retry["search_start"] == "2026-09-21T00:08:00+00:00"
    local = time_resolution.resolve(request(timezone="Australia/Melbourne"), prefs(), anchor)
    assert local["start"] == "2026-09-21T14:00:00+00:00"


def test_stable_slot_ids_and_participant_display_do_not_claim_attendance():
    first = option(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", MON, 30, "Australia/Melbourne", ["Asia/Kolkata"]
    )
    again = option(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", MON, 30, "Australia/Melbourne", ["Asia/Kolkata"]
    )
    assert first == again
    assert first.start_local == "2026-09-21T19:00:00+10:00"
    assert first.participant_displays[0]["start_local"] == "2026-09-21T14:30:00+05:30"


@pytest.mark.parametrize(
    "change",
    [
        {"date": "next Wednesday"},
        {"date": "2026-02-30"},
        {"days": 15},
        {"at_time": "4", "days": 2},
        {"at_time": "4pm", "meridiem": "AM"},
        {"at_time": "25:00"},
        {"at_time": "4ish"},
        {"fold": True, "at_time": "02:30"},
        {"duration_minutes": 481},
        {"count": 4},
        {"timezone": "localtime"},
        {"timezone": "Not/AZone"},
        {"participant_timezones": ["Mars/Office"]},
        {"participant_timezones": ["UTC", "UTC"]},
        {"anchor_at": MON.isoformat()},
    ],
)
def test_strict_bounded_input(change):
    with pytest.raises(ValueError):
        request(**change)


def test_adding_busy_never_creates_feasible_candidates_or_overlaps_buffers():
    rng = Random(80413)
    p = prefs(buffer_before_minutes=10, buffer_after_minutes=15)
    busy = []
    previous = set(engine.feasible_starts(MON, MON + timedelta(hours=8), 30, p, coverage(), MON))
    for _ in range(30):
        start = MON + timedelta(minutes=rng.randrange(-30, 480))
        busy.append((start, start + timedelta(minutes=rng.randrange(5, 90))))
        calendars = coverage(busy)
        actual = set(engine.feasible_starts(MON, MON + timedelta(hours=8), 30, p, calendars, MON))
        assert actual <= previous
        for at in actual:
            for lo, hi in busy:
                assert at + timedelta(minutes=30) <= lo - timedelta(
                    minutes=10
                ) or at >= hi + timedelta(minutes=15)
        previous = actual


def test_busy_fold_is_normalized_before_elapsed_buffers():
    from zoneinfo import ZoneInfo

    from app.schemas.calendar import BusyInterval, CalendarCoverage

    zone = ZoneInfo("Australia/Melbourne")
    # Second 02:10 is 16:10 UTC. Buffer arithmetic must not jump to the first fold.
    calendars = [
        CalendarCoverage(
            calendar_id="work",
            status="known",
            busy=[
                BusyInterval(
                    start=datetime(2026, 4, 5, 2, 10, tzinfo=zone, fold=1),
                    end=datetime(2026, 4, 5, 2, 20, tzinfo=zone, fold=1),
                )
            ],
        )
    ]
    from app.calendar.availability import expanded_busy

    intervals = expanded_busy(calendars, prefs(buffer_before_minutes=15, buffer_after_minutes=15))
    assert intervals == [
        (datetime(2026, 4, 4, 15, 55, tzinfo=UTC), datetime(2026, 4, 4, 16, 35, tzinfo=UTC))
    ]
