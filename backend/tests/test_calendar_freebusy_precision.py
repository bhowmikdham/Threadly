"""Independent provider-precision boundaries; mocked transport, no account access."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.api.errors import ApiError
from app.calendar import client


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2026, 10, 5, 13, 44, 57, 937225, UTC), datetime(2026, 10, 6, 13, 0, 0, 7, UTC)),
        (datetime(2026, 10, 5, 13, 44, 57, 100, UTC), datetime(2026, 10, 5, 13, 44, 57, 900, UTC)),
        (datetime(2026, 10, 5, 23, 59, 59, 999999, UTC), datetime(2026, 10, 6, 0, 0, 0, 1, UTC)),
    ],
)
async def test_wire_window_covers_request_and_result_clips_to_original(start, end):
    def provider(request):
        sent = json.loads(request.content)
        wire_start, wire_end = instant(sent["timeMin"]), instant(sent["timeMax"])
        assert wire_start <= start < end <= wire_end
        assert start - wire_start < timedelta(seconds=1)
        assert wire_end - end < timedelta(seconds=1)
        # Google preserves milliseconds, not the microseconds of a fresh chat anchor.
        assert wire_start.microsecond % 1000 == wire_end.microsecond % 1000 == 0
        return httpx.Response(
            200,
            json={
                "timeMin": wire_start.isoformat(timespec="milliseconds"),
                "timeMax": wire_end.isoformat(timespec="milliseconds"),
                "calendars": {
                    "synthetic": {
                        "busy": [{"start": wire_start.isoformat(), "end": wire_end.isoformat()}]
                    }
                },
            },
        )

    result = await client.freebusy(
        "fixture-token", ["synthetic"], start, end, transport=httpx.MockTransport(provider)
    )
    assert result[0].status == "known"
    assert [(b.start, b.end) for b in result[0].busy] == [(start, end)]


@pytest.mark.parametrize(
    "edge,delta", [("timeMin", -1), ("timeMin", 1), ("timeMax", -1), ("timeMax", 1)]
)
async def test_echo_must_match_transmitted_window_even_when_difference_is_small(edge, delta):
    start = datetime(2026, 10, 5, 13, 44, 57, 937225, UTC)
    end = start + timedelta(minutes=30)

    def provider(request):
        sent = json.loads(request.content)
        echoed = {key: instant(sent[key]) for key in ("timeMin", "timeMax")}
        echoed[edge] += timedelta(milliseconds=delta)
        return httpx.Response(
            200,
            json={
                **{key: value.isoformat(timespec="milliseconds") for key, value in echoed.items()},
                "calendars": {"synthetic": {"busy": []}},
            },
        )

    with pytest.raises(ApiError) as error:
        await client.freebusy(
            "fixture-token", ["synthetic"], start, end, transport=httpx.MockTransport(provider)
        )
    assert error.value.code == "calendar_response_invalid"


@pytest.mark.parametrize("instruction", ["check my availability today", "am i busy today"])
@pytest.mark.parametrize("busy", [False, True])
async def test_reported_today_questions_after_melbourne_midnight(monkeypatch, instruction, busy):
    from types import SimpleNamespace

    from app.calendar import day_availability
    from app.schemas.calendar import FreeBusyOut
    from app.schemas.conversation import CheckDayAvailability

    anchor = datetime(2026, 10, 5, 13, 36, 40, 937225, UTC)
    end = datetime(2026, 10, 6, 13, tzinfo=UTC)
    calls = []

    def provider(req):
        sent = json.loads(req.content)
        calls.append(sent)
        return httpx.Response(
            200,
            json={
                **{
                    key: instant(sent[key]).isoformat(timespec="milliseconds")
                    for key in ("timeMin", "timeMax")
                },
                "calendars": {
                    "owned": {
                        "busy": [
                            {
                                "start": "2026-10-06T03:00:00Z",
                                "end": "2026-10-06T04:00:00Z",
                            }
                        ]
                        if busy
                        else []
                    }
                },
            },
        )

    async def prefs(owner):
        assert owner == 42
        return SimpleNamespace(
            version=17,
            account_version=2,
            preferences=SimpleNamespace(timezone="Australia/Melbourne"),
        )

    async def query(owner, body):
        assert owner == 42 and body.expected_preferences_version == 17
        assert body.start == anchor and body.end == end
        calendars = await client.freebusy(
            "fixture", ["owned"], body.start, body.end, transport=httpx.MockTransport(provider)
        )
        return FreeBusyOut(
            id="synthetic-owned",
            preferences_version=17,
            account_version=2,
            policy_version="calendar-read-1.0.0",
            checked_at=anchor,
            expires_at=anchor + timedelta(minutes=5),
            start=body.start,
            end=body.end,
            coverage="complete",
            calendars=calendars,
        )

    monkeypatch.setattr(day_availability.service, "get_preferences", prefs)
    monkeypatch.setattr(day_availability.service, "query_freebusy", query)
    result = await day_availability.answer(
        42,
        instruction,
        anchor=anchor,
        window=CheckDayAvailability(date_phrase="today", date_source="today"),
    )
    assert "error_code" not in result, result
    assert result["calendar_availability"]["date"] == "2026-10-06"
    assert instant(result["calendar_availability"]["start"]) == anchor
    assert instant(result["calendar_availability"]["end"]) == end
    assert "rest of Tuesday, 6 October 2026" in result["text"]
    assert ("2:00 PM–3:00 PM" if busy else "no busy time recorded") in result["text"]
    assert len(calls) == 1


@pytest.mark.parametrize("padding_only", [False, True])
async def test_padding_is_removed_and_unknown_calendar_remains_unknown(padding_only):
    start = datetime.fromisoformat("2026-10-06T00:36:40.937225+11:00")
    end = start + timedelta(seconds=2)

    def provider(req):
        sent = json.loads(req.content)
        lo, hi = instant(sent["timeMin"]), instant(sent["timeMax"])
        assert lo == start.replace(microsecond=0)
        assert hi == end.replace(microsecond=0) + timedelta(seconds=1)
        assert req.method == "POST" and req.url.path == "/calendar/v3/freeBusy"
        assert sent["items"] == [{"id": "owned"}, {"id": "unknown"}]
        pairs = (
            [(lo, start), (end, hi)]
            if padding_only
            else [
                (lo, start + timedelta(microseconds=1)),
                (end - timedelta(microseconds=1), hi),
            ]
        )
        return httpx.Response(
            200,
            json={
                "timeMin": lo.isoformat(timespec="milliseconds"),
                "timeMax": hi.isoformat(timespec="milliseconds"),
                "calendars": {
                    "owned": {
                        "busy": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in pairs]
                    },
                    "unknown": {"errors": [{"reason": "notFound"}]},
                },
            },
        )

    result = await client.freebusy(
        "fixture", ["owned", "unknown"], start, end, transport=httpx.MockTransport(provider)
    )
    expected = (
        []
        if padding_only
        else [
            (start, start + timedelta(microseconds=1)),
            (end - timedelta(microseconds=1), end),
        ]
    )
    assert result[0].status == "known"
    assert [(b.start, b.end) for b in result[0].busy] == expected
    assert result[1].status == "unknown" and result[1].busy == []


async def test_aligned_window_is_unchanged():
    start = datetime(2026, 10, 5, 13, tzinfo=UTC)
    end = start + timedelta(days=1)

    def provider(req):
        sent = json.loads(req.content)
        assert instant(sent["timeMin"]) == start
        assert instant(sent["timeMax"]) == end
        return httpx.Response(
            200,
            json={
                "timeMin": start.isoformat(timespec="milliseconds"),
                "timeMax": end.isoformat(timespec="milliseconds"),
                "calendars": {"owned": {"busy": []}},
            },
        )

    result = await client.freebusy(
        "fixture", ["owned"], start, end, transport=httpx.MockTransport(provider)
    )
    assert result[0].status == "known" and result[0].busy == []
