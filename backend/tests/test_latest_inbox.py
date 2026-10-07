"""Latest-mail retrieval, display-only cleaning and recipient-local chronology."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.assistant import inbox_chat
from app.conversation import engine, evaluate
from app.conversation.runtime import Runtime
from app.mail.presentation import received_display, snippet
from app.schemas.conversation import ConversationTurn, SearchMail
from app.schemas.inbox_chat import InboxFilters
from app.sync.worker import _message_values

ZONE = "Australia/Melbourne"
NOW = datetime(2026, 10, 6, 12, 20, 0, 500000, tzinfo=UTC)


def filters(**changes):
    return InboxFilters(
        schema_version="1.0",
        received_from=NOW - timedelta(days=1),
        received_before=NOW,
        **{"folder": "INBOX", "timezone": ZONE, **changes},
    )


def message(identifier, received_at, labels=("INBOX",), body="Hello"):
    return {
        "gmail_msg_id": identifier,
        "gmail_thread_id": identifier,
        "subject": "Subject",
        "from_addr": "Sender <sender@example.test>",
        "received_at": received_at,
        "body_clean": body,
        "reply_metadata": {"label_ids": labels, "addresses": {"from": ["sender@example.test"]}},
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        ("&amp;zwnj;\u200c\u200c \u200b &nbsp; **Hello** &amp; welcome", "Hello & welcome"),
        ("Your order is ready. https://track.example/t?id=1&token=private", "Your order is ready."),
        ("[Open receipt](https://track.example/a)\\*\\*today\\*\\*", "Open receipttoday"),
        ("Hello\nUnsubscribe https://example.test/leave\nA footer address", "Hello"),
        ("می\u200cخواهم 👩\u200d💻", "می\u200cخواهم 👩\u200d💻"),
        ("Café &amp; tea\nContact <help@example.test>", "Café & tea Contact <help@example.test>"),
        ("a" * 230, "a" * 220),
    ],
)
def test_display_snippet_removes_padding_tracking_and_footer_without_destroying_words(
    value, expected
):
    assert snippet(value) == expected


@pytest.mark.parametrize(
    "instant,local,abbreviation",
    [
        ("2026-10-03T15:59:00Z", "2026-10-04T01:59:00+10:00", "AEST"),
        ("2026-10-03T16:01:00Z", "2026-10-04T03:01:00+11:00", "AEDT"),
        ("2026-04-04T15:59:00Z", "2026-04-05T02:59:00+11:00", "AEDT"),
        ("2026-04-04T16:01:00Z", "2026-04-05T02:01:00+10:00", "AEST"),
        ("2026-10-06T14:30:00Z", "2026-10-07T01:30:00+11:00", "AEDT"),
    ],
)
def test_local_display_uses_instant_specific_dst_and_date(instant, local, abbreviation):
    result = received_display(instant, ZONE)
    assert result["received_at_local"] == local
    assert abbreviation in result["received_at_display"]
    assert ZONE in result["received_at_display"]
    assert result["timestamp_source"] == "gmail.internalDate"


def test_received_timestamp_uses_internal_date_not_forged_date_header():
    raw = {
        "id": "aa",
        "threadId": "bb",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {"headers": [{"name": "Date", "value": "Tue, 6 Oct 2020 11:26:00 +0000"}]},
    }
    normalized = _message_values(raw, "owner@example.test")
    assert normalized["received_at"] == NOW
    assert normalized["sent_at"].year == 2020


@pytest.mark.parametrize("zone", ["Not/AZone", "", "UTC+11"])
def test_filters_validate_timezone(zone):
    with pytest.raises(ValidationError):
        filters(timezone=zone)


def test_melbourne_local_day_handles_spring_dst_boundary():
    start, end = inbox_chat.date_window("yesterday", datetime(2026, 10, 5, tzinfo=UTC), ZONE)
    assert start == datetime(2026, 10, 3, 14, tzinfo=UTC)
    assert end == datetime(2026, 10, 4, 13, tzinfo=UTC)
    assert end - start == timedelta(hours=23)


async def test_inbox_filters_archive_sorts_instants_and_preserves_raw_source(monkeypatch):
    raw = message("old", "2026-10-06T11:26:00+11:00", body="&amp;zwnj;Hello https://t.test/a")
    original = deepcopy(raw)
    seen = []

    async def page(owner, query, scope, cursor, *, page_size):
        seen.append(query)
        return [
            raw,
            message("archived", "2026-10-06T12:00:00Z", labels=("IMPORTANT",)),
            message("new", "2026-10-06T01:00:00Z"),
        ], None

    monkeypatch.setattr(inbox_chat.mail_search, "page", page)
    result = await inbox_chat.search(7, filters())
    assert seen[0].endswith(" in:inbox")
    assert [r["message_id"] for r in result["results"]] == ["new", "old"]
    assert result["results"][1]["received_at"] == original["received_at"]
    assert result["results"][1]["snippet"] == "Hello"
    assert raw == original
    assert result["coverage"]["complete"] is False
    all_mail = await inbox_chat.search(7, filters(folder="all_mail"))
    assert [r["message_id"] for r in all_mail["results"]] == ["archived", "new", "old"]


async def test_exact_date_bounds_keep_start_and_fractional_final_second(monkeypatch):
    start = NOW - timedelta(days=1)
    seen = []

    async def page(owner, query, scope, cursor, *, page_size):
        seen.append(query)
        return [
            message("at-start", start.isoformat()),
            message("too-old", (start - timedelta(milliseconds=1)).isoformat()),
            message("last-fraction", (NOW - timedelta(milliseconds=1)).isoformat()),
            message("at-end", NOW.isoformat()),
        ], None

    monkeypatch.setattr(inbox_chat.mail_search, "page", page)
    result = await inbox_chat.search(7, filters())
    assert [r["message_id"] for r in result["results"]] == ["last-fraction", "at-start"]
    assert f"after:{int(start.timestamp()) - 1}" in seen[0]
    assert f"before:{int(NOW.timestamp()) + 1}" in seen[0]


async def test_empty_provider_page_retains_cursor_then_returns_next_inbox_result(monkeypatch):
    calls = []

    async def page(owner, query, scope, cursor, *, page_size):
        calls.append((cursor, page_size))
        if cursor is None:
            return [], "next"
        return [message("new", "2026-10-06T11:26:00Z")], None

    monkeypatch.setattr(inbox_chat.mail_search, "page", page)
    result = await inbox_chat.search(7, filters(limit=1))
    assert calls == [(None, 1), ("next", 1)]
    assert result["results"][0]["received_at_display"].startswith("06 Oct 2026, 10:26 PM AEDT")
    assert result["next_cursor"] is None


@pytest.mark.parametrize(
    "selection,limit,count", [("latest_message", 5, 1), ("recent_matches", 2, 2)]
)
async def test_typed_selection_controls_count_and_retains_current_zone(
    monkeypatch, selection, limit, count
):
    observed = []

    async def search(owner, resolved, cursor):
        observed.append(resolved)
        return {
            "filters": resolved.model_dump(mode="json"),
            "results": [],
            "next_cursor": None,
            "coverage": {"complete": False},
        }

    monkeypatch.setattr(inbox_chat, "search", search)
    monkeypatch.setattr(
        "app.conversation.runtime.get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    request = ConversationTurn(
        expected_version=0,
        conversation_id="83bba6ec-4a4c-4efe-b2f9-e9b579864179",
        request_id="6a78f4ac-cf54-4d84-a85c-4d3337d2f11b",
        instruction="Could you check the latest mail that I got in my inbox",
        timezone=ZONE,
    )
    runtime = Runtime(7, request, {"history": [], "refs": {}, "result_order": []}, None)
    await runtime.search(SearchMail(query="", folder="INBOX", selection=selection, limit=limit))
    assert (observed[0].limit, observed[0].folder, observed[0].timezone) == (count, "INBOX", ZONE)


async def test_versioned_synthetic_replay_delivers_one_card_with_local_time():
    case = next(c for c in evaluate.CASES if c["id"] == "latest_single_inbox_local_time")
    runtime = evaluate.FixtureRuntime(case, case["turns"][0])

    class Model:
        calls = 0

        async def decide(self, prompt, messages, config):
            self.calls += 1
            if self.calls == 1:
                tool, values = (
                    "search_mail",
                    {
                        "query": "",
                        "folder": "INBOX",
                        "selection": "latest_message",
                    },
                )
            else:
                row = messages[-1]["content"][0]["toolResult"]["content"][0]["json"]["results"][0]
                assert row["received_at"] == "2026-09-23T10:00:00Z"
                assert row["received_at_local"] == "2026-09-23T20:00:00+10:00"
                tool, values = (
                    "respond",
                    {
                        "kind": "message",
                        "text": f"I found this email, received {row['received_at_display']}.",
                    },
                )
            return {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": f"fixture-{self.calls}",
                            "name": tool,
                            "input": values,
                        }
                    }
                ],
            }

    result = await engine.run({"user_turn": case["turns"][0], "timezone": ZONE}, runtime, Model())
    assert evaluate.grade(case, result, runtime.calls, runtime.search_page) == []
    assert result["release"] == "contextual-conversation-1.8.8"
    assert len(runtime.search_page["results"]) == 1
    assert runtime.search_page["results"][0]["reference"] == "mail-1"


def test_latest_replay_rejects_five_item_default_and_unzoned_utc_answer():
    case = next(c for c in evaluate.CASES if c["id"] == "latest_single_inbox_local_time")
    failures = evaluate.grade(
        case,
        {"kind": "message", "text": "I found five emails; the first arrived at 10:00 AM."},
        [{"name": "search_mail", "input": {"query": "", "folder": "INBOX", "limit": 5}}],
        {"results": [{"reference": f"mail-{i}"} for i in range(1, 6)]},
    )
    assert set(failures) == {
        "wrong_single_inbox_search",
        "single_inbox_card_count_mismatch",
        "inbox_answer_count_mismatch",
        "inbox_category_scope_mismatch",
        "mail_time_mismatch",
        "mail_time_missing_zone",
    }


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I found this email.", []),
        ("It arrived at 8 PM AEST on 23 September 2026.", []),
        ("It arrived at 20:00 Australia/Melbourne (UTC+10:00) on September 23, 2026.", []),
        ("It arrived at 7:00 PM AEST.", ["mail_time_mismatch"]),
        ("It arrived at 10:00 Australia/Melbourne.", ["mail_time_mismatch"]),
        ("It arrived at 20:00 AEDT.", ["mail_time_missing_zone", "mail_timezone_mismatch"]),
        ("It arrived at 20:00 AEST (UTC+11:00).", ["mail_timezone_mismatch"]),
        ("It arrived on 24 Sep 2026.", ["mail_date_mismatch"]),
        ("It arrived on 2026-09-24.", ["mail_date_mismatch"]),
    ],
)
def test_replay_grades_optional_clocks_dates_and_dst_against_provider_instant(text, expected):
    assert (
        evaluate._mail_display_failures(text, {"received_at": "2026-09-23T10:00:00Z"}) == expected
    )


async def test_replay_explicit_count_overrides_conflicting_single_selection():
    case = next(c for c in evaluate.CASES if c["id"] == "latest_two_inbox_after_merchant")
    runtime = evaluate.FixtureRuntime(case, case["turns"][0])
    await runtime.call(
        "search_mail", SearchMail(query="", folder="INBOX", selection="latest_message")
    )
    assert runtime.search_page["filters"]["limit"] == 2
