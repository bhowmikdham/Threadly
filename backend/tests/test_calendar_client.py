"""Independent transport and unknown-coverage regressions; no live Google traffic."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.api.errors import ApiError
from app.calendar import client
from app.schemas.calendar import FreeBusyRequest, Preferences

START = datetime(2026, 10, 1, tzinfo=UTC)
END = START + timedelta(days=1)


def preferences(**changes):
    return dict(
        {
            "timezone": "Australia/Melbourne",
            "calendar_ids": ["work@example.test"],
            "working_periods": [{"weekday": 0, "start_minute": 540, "end_minute": 1020}],
            "buffer_before_minutes": 10,
            "buffer_after_minutes": 10,
            "minimum_notice_minutes": 60,
            "default_duration_minutes": 30,
        },
        **changes,
    )


def response(calendars):
    return {"timeMin": START.isoformat(), "timeMax": END.isoformat(), "calendars": calendars}


@pytest.mark.parametrize(
    "change",
    [
        {"timezone": "Mars/Office"},
        {"calendar_ids": ["x", "x"]},
        {"calendar_ids": ["x\n"]},
        {"calendar_ids": [str(i) for i in range(11)]},
        {"calendar_ids": []},
        {"default_duration_minutes": True},
        {"minimum_notice_minutes": -1},
        {"buffer_before_minutes": 241},
        {"default_duration_minutes": 481},
        {"working_periods": [{"weekday": 0, "start_minute": 600, "end_minute": 500}]},
        {"working_periods": [{"weekday": 7, "start_minute": 600, "end_minute": 700}]},
        {
            "working_periods": [
                {"weekday": 0, "start_minute": 600, "end_minute": 700},
                {"weekday": 0, "start_minute": 650, "end_minute": 750},
            ]
        },
        {"model_calendar_id": "invented"},
    ],
)
def test_invalid_preferences(change):
    with pytest.raises(ValueError):
        Preferences.model_validate(preferences(**change))


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-01T09:00:00", END),
        (START, START),
        (END, START),
        (START, START + timedelta(days=32)),
        (123456789, END),
    ],
)
def test_invalid_window(start, end):
    with pytest.raises(ValueError):
        FreeBusyRequest(expected_preferences_version=1, start=start, end=end)


def test_intervals_clipped_merged_and_offsets_normalized():
    data = response(
        {
            "one": {
                "busy": [
                    {"start": "2026-10-01T10:00:00+10:00", "end": "2026-10-01T12:00:00+10:00"},
                    {"start": "2026-10-01T02:00:00Z", "end": "2026-10-01T03:00:00Z"},
                    {"start": "2026-09-30T23:00:00Z", "end": "2026-10-01T01:00:00Z"},
                    {"start": "2026-10-02T02:00:00Z", "end": "2026-10-02T03:00:00Z"},
                ]
            }
        }
    )
    result = client.normalize(data, ["one"], START, END)[0]
    assert result.status == "known"
    assert [(b.start, b.end) for b in result.busy] == [(START, START + timedelta(hours=3))]


@pytest.mark.parametrize(
    "bad,reason",
    [
        (None, "missing"),
        ({"errors": [{"reason": "future-new-error"}], "busy": []}, "provider_error"),
        ({}, "malformed"),
        ({"busy": None}, "malformed"),
        ([], "malformed"),
        ({"busy": [{"start": "2026-10-01T10:00:00", "end": END.isoformat()}]}, "malformed"),
        ({"busy": [{"start": END.isoformat(), "end": START.isoformat()}]}, "malformed"),
    ],
)
def test_unknown_never_becomes_known_empty(bad, reason):
    result = client.normalize(
        response({"one": {"busy": []}, "two": bad}), ["one", "two"], START, END
    )
    assert result[0].status == "known" and result[0].busy == []
    assert result[1].status == "unknown" and result[1].reason == reason
    assert result[1].busy == []


def test_interval_and_response_window_limits():
    interval = {"start": START.isoformat(), "end": END.isoformat()}
    with pytest.raises(ApiError) as error:
        client.normalize(response({"one": {"busy": [interval] * 2001}}), ["one"], START, END)
    assert error.value.code == "calendar_interval_limit"
    with pytest.raises(ApiError):
        client.normalize(
            {"timeMin": START.isoformat(), "timeMax": START.isoformat()}, [], START, END
        )


@pytest.mark.asyncio
async def test_paginated_list_and_freebusy_fixed_transport():
    calls = []

    def handler(req):
        calls.append(req)
        assert req.headers["authorization"] == "Bearer fixture-token"
        if req.url.path.endswith("calendarList"):
            assert req.method == "GET" and req.url.params["showHidden"] == "true"
            if "pageToken" not in req.url.params:
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"id": "read", "accessRole": "freeBusyReader", "summary": "Busy only"},
                        ],
                        "nextPageToken": "second",
                    },
                )
            return httpx.Response(200, json={"items": [{"id": "write", "accessRole": "owner"}]})
        assert req.method == "POST" and req.url.path == "/calendar/v3/freeBusy"
        return httpx.Response(200, json=response({"read": {"busy": []}}))

    transport = httpx.MockTransport(handler)
    items = await client.list_calendars("fixture-token", transport=transport)
    assert items[0]["can_read_busy"] and not items[0]["event_write_acl"]
    assert items[1]["event_write_acl"]
    result = await client.freebusy("fixture-token", ["read"], START, END, transport=transport)
    assert result[0].status == "known" and len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["cycle", "too_many_pages", "duplicate", "bad_id", "oversize"])
async def test_bad_listing_stops_bounded(case):
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        if case == "oversize":
            return httpx.Response(200, content=b" " * (client.MAX_RESPONSE_BYTES + 1))
        items = [{"id": "\n" if case == "bad_id" else "one", "accessRole": "reader"}]
        if case in {"cycle", "too_many_pages"}:
            items = []
        return httpx.Response(
            200,
            json={
                "items": items,
                "nextPageToken": str(calls) if case == "too_many_pages" else "same",
            },
        )

    with pytest.raises(ApiError):
        await client.list_calendars("secret", transport=httpx.MockTransport(handler))
    assert calls <= 10


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
async def test_errors_are_sanitized_and_no_retries(status, caplog):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"secret": "synthetic-sensitive-calendar"})

    with pytest.raises(ApiError) as error:
        await client.list_calendars("secret", transport=httpx.MockTransport(handler))
    assert len(calls) == 1
    assert "synthetic-sensitive-calendar" not in error.value.message + caplog.text


@pytest.mark.asyncio
async def test_network_timeout_is_sanitized():
    def handler(request):
        raise httpx.ReadTimeout("synthetic-secret", request=request)

    with pytest.raises(ApiError) as error:
        await client.list_calendars("secret", transport=httpx.MockTransport(handler))
    assert error.value.code == "calendar_unavailable" and "secret" not in error.value.message


def test_no_write_scope_required_for_reads_and_write_handler_stays_disabled():
    from app.capabilities.service import build_capabilities
    from tests.test_google_capabilities import _by_id, _user

    for scopes in (
        ["https://www.googleapis.com/auth/calendar.readonly"],
        ["https://www.googleapis.com/auth/calendar"],
    ):
        result = _by_id(build_capabilities(_user(scopes=scopes)))
        assert result["calendar_read"]["ready"] and result["calendar_list"]["ready"]
        assert result["calendar_write"]["ready"] is False


def test_timezone_data_available_without_system_database():
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from datetime import datetime, timedelta; from zoneinfo import ZoneInfo; "
                "z = ZoneInfo('Australia/Melbourne'); "
                "assert datetime(2026,1,1,tzinfo=z).utcoffset() == timedelta(hours=11); "
                "assert datetime(2026,7,1,tzinfo=z).utcoffset() == timedelta(hours=10)"
            ),
        ],
        env={**os.environ, "PYTHONTZPATH": ""},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
