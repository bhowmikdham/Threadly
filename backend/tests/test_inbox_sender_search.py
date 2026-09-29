"""An explicit sender search must not display matches from other From addresses."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.assistant import inbox_chat
from app.schemas.inbox_chat import InboxFilters

START = datetime(2026, 9, 23, tzinfo=UTC)
END = START + timedelta(days=1)


def filters(**changes):
    return InboxFilters.model_validate(
        {
            "schema_version": "1.0",
            "query": "",
            "folder": "all_mail",
            "received_from": START,
            "received_before": END,
            **changes,
        }
    )


def message(identifier, sender, *, label="INBOX", received_at=None):
    return {
        "gmail_msg_id": identifier,
        "gmail_thread_id": identifier,
        "subject": "A subject",
        "from_addr": f"Sender <{sender}>",
        "received_at": (received_at or START + timedelta(hours=1)).isoformat(),
        "body_clean": "A bounded message body",
        "reply_metadata": {
            "addresses": {"from": [sender.casefold()]},
            "label_ids": [label],
        },
    }


async def test_exact_sender_search_filters_mixed_gmail_page_and_passes_limit(monkeypatch):
    calls = []

    async def page(owner, query, scope, cursor, *, page_size):
        calls.append((owner, query, scope, cursor, page_size))
        return [
            message("other", "someone@example.test"),
            message("match", "naveen@example.test"),
            message("partial", "naveen@other.example.test"),
        ], "next-page"

    monkeypatch.setattr(inbox_chat.mail_search, "page", page)

    result = await inbox_chat.search(7, filters(sender_email="Naveen@Example.Test", limit=2))

    assert [row["message_id"] for row in result["results"]] == ["match"]
    assert result["next_cursor"] == "next-page"
    assert result["coverage"]["page_size"] == 2
    assert calls[0][0] == 7
    assert calls[0][1].startswith("from:naveen@example.test -in:spam -in:trash ")
    assert calls[0][3:] == (None, 2)


async def test_sender_search_keeps_explicit_phrase_date_and_folder_bounds(monkeypatch):
    observed = []

    async def page(owner, query, scope, cursor, *, page_size):
        observed.append(query)
        return [
            message("wanted", "naveen@example.test"),
            message("sent", "naveen@example.test", label="SENT"),
            message("old", "naveen@example.test", received_at=START - timedelta(days=1)),
            message("other", "else@example.test"),
        ], None

    monkeypatch.setattr(inbox_chat.mail_search, "page", page)

    result = await inbox_chat.search(
        7,
        filters(query="invoice", sender_email="naveen@example.test", folder="INBOX"),
    )

    assert [row["message_id"] for row in result["results"]] == ["wanted"]
    assert observed[0].startswith('"invoice" from:naveen@example.test ')
    assert f"after:{int(START.timestamp())}" in observed[0]
    assert f"before:{int(END.timestamp())}" in observed[0]
    assert observed[0].endswith(" in:inbox")


async def test_sender_and_limit_are_bound_to_search_cursor_scope(monkeypatch):
    calls = []

    async def page(owner, query, scope, cursor, *, page_size):
        calls.append((scope, cursor, page_size))
        return [], None

    monkeypatch.setattr(inbox_chat.mail_search, "page", page)

    await inbox_chat.search(7, filters(sender_email="naveen@example.test"), "opaque-cursor")
    await inbox_chat.search(7, filters(sender_email="other@example.test"), "opaque-cursor")
    await inbox_chat.search(
        7, filters(sender_email="naveen@example.test", limit=2), "opaque-cursor"
    )
    await inbox_chat.search(
        7, filters(sender_email="naveen@example.test", folder="INBOX"), "opaque-cursor"
    )

    assert len({scope for scope, _, _ in calls}) == len(calls)
    assert all(cursor == "opaque-cursor" for _, cursor, _ in calls)
    assert [size for _, _, size in calls] == [5, 5, 2, 5]


async def test_local_false_positive_is_skipped_and_next_provider_page_fills_limit(monkeypatch):
    calls = []

    async def page(owner, query, scope, cursor, *, page_size):
        calls.append((cursor, page_size))
        if cursor is None:
            return [
                message("first", "naveen@example.test"),
                message("wrong", "someone@example.test"),
            ], "next-page"
        return [message("second", "naveen@example.test")], "later-page"

    monkeypatch.setattr(inbox_chat.mail_search, "page", page)

    result = await inbox_chat.search(7, filters(sender_email="naveen@example.test", limit=2))

    assert [row["message_id"] for row in result["results"]] == ["first", "second"]
    assert result["next_cursor"] == "later-page"
    assert result["coverage"]["provider_pages_read"] == 2
    assert result["coverage"]["provider_candidates_read"] == 3
    assert calls == [(None, 2), ("next-page", 1)]


async def test_search_stops_after_five_false_positive_pages(monkeypatch):
    calls = []

    async def page(owner, query, scope, cursor, *, page_size):
        calls.append((cursor, page_size))
        return [message(f"wrong-{len(calls)}", "someone@example.test")], f"page-{len(calls)}"

    monkeypatch.setattr(inbox_chat.mail_search, "page", page)

    result = await inbox_chat.search(7, filters(sender_email="naveen@example.test", limit=2))

    assert result["results"] == []
    assert result["next_cursor"] == "page-5"
    assert result["coverage"]["provider_pages_read"] == 5
    assert len(calls) == 5


@pytest.mark.parametrize(
    "sender",
    ["from:naveen@example.test", "naveen@example.test OR", "a@example.com b@example.com"],
)
def test_sender_filter_rejects_provider_operators_and_multiple_addresses(sender):
    with pytest.raises(ValidationError):
        filters(sender_email=sender)
