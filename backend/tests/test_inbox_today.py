"""Today's absence is proved separately from the latest result, with identical scope."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.api.errors import ApiError
from app.assistant import inbox_chat
from app.conversation.runtime import Runtime
from app.mail.inbox_status import check_today
from app.schemas.conversation import ConversationTurn, SearchMail
from app.schemas.inbox_chat import InboxFilters

ZONE = "Australia/Melbourne"
NOW = datetime(2026, 10, 6, 13, 20, tzinfo=UTC)


def scope(**changes):
    return InboxFilters(
        schema_version="1.0",
        received_from=NOW - timedelta(days=365),
        received_before=NOW,
        **{"folder": "INBOX", "inbox_category": "primary", "timezone": ZONE, "limit": 1, **changes},
    )


def page(filters, rows=(), cursor=None, exhausted=True):
    return {
        "filters": filters.model_dump(mode="json"),
        "results": list(rows),
        "next_cursor": cursor,
        "coverage": {"complete": False, "provider_exhausted": exhausted},
    }


@pytest.mark.parametrize("category", ["primary", "all"])
@pytest.mark.parametrize(
    "anchor,start",
    [
        (NOW, "2026-10-06T13:00:00+00:00"),
        (datetime(2026, 10, 4, 12, 59, tzinfo=UTC), "2026-10-03T14:00:00+00:00"),  # 23h DST day
        (datetime(2026, 4, 5, 13, 59, tzinfo=UTC), "2026-04-04T13:00:00+00:00"),  # 25h DST day
    ],
)
async def test_empty_today_proof_uses_same_scope_and_true_local_midnight(
    monkeypatch, category, anchor, start
):
    seen = []

    async def search(owner, filters, cursor):
        seen.append((owner, filters, cursor))
        return page(filters)

    monkeypatch.setattr(inbox_chat, "search", search)
    original = scope(inbox_category=category, sender_email="sender@example.test", query="receipt")
    result = await check_today(7, original, anchor)
    assert result["status"] == "no_messages" and result["coverage_complete"]
    assert result["received_from"] == start.replace("+00:00", "Z")
    assert result["local_date"] == anchor.astimezone(ZoneInfo(ZONE)).date().isoformat()
    _, actual, cursor = seen[0]
    assert actual.received_before == anchor and actual.received_from.isoformat() == start
    assert (actual.folder, actual.inbox_category, actual.query, actual.sender_email) == (
        "INBOX",
        category,
        "receipt",
        "sender@example.test",
    )
    assert original.received_from == NOW - timedelta(days=365) and cursor is None


@pytest.mark.parametrize(
    "rows,cursor,exhausted,expected",
    [
        ([], None, True, "no_messages"),
        ([{"message_id": "a2"}], "more", False, "has_messages"),
        ([], "more", False, "unknown"),
        ([], None, False, "unknown"),
        ([], "more", True, "unknown"),
    ],
)
async def test_today_status_distinguishes_presence_absence_and_incomplete(
    monkeypatch, rows, cursor, exhausted, expected
):
    async def search(owner, filters, token):
        return page(filters, rows, cursor, exhausted)

    monkeypatch.setattr(inbox_chat, "search", search)
    result = await check_today(7, scope(), NOW)
    assert result["status"] == expected


@pytest.mark.parametrize(
    "status,code",
    [
        (503, "gmail_unavailable"),
        (401, "gmail_reauth_required"),
        (403, "gmail_access_denied"),
        (409, "google_connection_changed"),
    ],
)
async def test_provider_failure_is_unknown_but_security_failures_stop(monkeypatch, status, code):
    async def search(*args):
        raise ApiError(status, code, "Synthetic failure")

    monkeypatch.setattr(inbox_chat, "search", search)
    if status >= 500:
        result = await check_today(7, scope(), NOW)
        assert result["status"] == "unknown" and result["reason"] == "provider_unavailable"
    else:
        with pytest.raises(ApiError) as exc:
            await check_today(7, scope(), NOW)
        assert exc.value.code == code


@pytest.mark.parametrize("category", ["primary", "all"])
@pytest.mark.parametrize(
    "received_at,today_rows,expected",
    [
        ("2026-10-06T11:09:44Z", [], "no_messages"),
        ("2026-10-06T13:09:44Z", [{"message_id": "a2"}], "has_messages"),
        (None, [], "no_messages"),
    ],
)
async def test_latest_and_today_are_independent_and_proof_reaches_model_and_cards(
    monkeypatch, category, received_at, today_rows, expected
):
    seen = []

    async def search(owner, filters, cursor):
        seen.append(filters)
        if len(seen) == 2:
            return page(filters, today_rows)
        rows = (
            []
            if received_at is None
            else [{"message_id": "a1", "thread_id": "a1", "received_at": received_at}]
        )
        return page(filters, rows, "original-next-page", False)

    monkeypatch.setattr(inbox_chat, "search", search)
    monkeypatch.setattr(
        "app.conversation.runtime.get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    request = ConversationTurn(
        conversation_id=str(uuid4()),
        request_id=str(uuid4()),
        expected_version=0,
        instruction="Check the latest email in my inbox",
        timezone=ZONE,
    )
    runtime = Runtime(7, request, {"history": [], "refs": {}, "result_order": []}, None)
    runtime.mail_anchor = NOW
    result = await runtime.search(
        SearchMail(query="", folder="INBOX", selection="latest_message", inbox_category=category)
    )
    assert len(seen) == 2 and seen[0].received_from == NOW - timedelta(days=365)
    assert seen[1].received_from == datetime(2026, 10, 6, 13, tzinfo=UTC)
    assert all(f.inbox_category == category for f in seen)
    assert result["today_check"]["status"] == expected
    assert result["today_check"] == runtime.search_page["today_check"]
    assert runtime.state["search"]["next_cursor"] == "original-next-page"
    if received_at:
        assert result["results"][0]["received_at"] == received_at
        assert runtime.state["refs"]["mail-1"] == {"message_id": "a1", "thread_id": "a1"}
    else:
        assert result["results"] == []


@pytest.mark.parametrize(
    "instruction,sender",
    [
        ("Show emails from primary@example.test", "primary@example.test"),
        ("Show emails from inbox@example.test", "inbox@example.test"),
        ("Show emails from teacher@example.test about primary school", "teacher@example.test"),
    ],
)
async def test_sender_search_does_not_turn_incidental_primary_into_inbox(
    monkeypatch, instruction, sender
):
    seen = []

    async def search(owner, filters, cursor):
        seen.append(filters)
        return page(filters)

    monkeypatch.setattr(inbox_chat, "search", search)
    monkeypatch.setattr(
        "app.conversation.runtime.get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    request = ConversationTurn(
        conversation_id=str(uuid4()),
        request_id=str(uuid4()),
        expected_version=0,
        instruction=instruction,
        timezone=ZONE,
    )
    runtime = Runtime(7, request, {"history": [], "refs": {}, "result_order": []}, None)
    await runtime.search(SearchMail(query="", sender_email=sender, folder="all_mail"))
    assert seen[0].folder == "all_mail" and seen[0].inbox_category == "all"


async def test_today_timeout_is_unknown_and_does_not_fail_latest(monkeypatch):
    import asyncio

    from app.mail import inbox_status

    async def never_returns(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(inbox_chat, "search", never_returns)
    monkeypatch.setattr(inbox_status, "TODAY_CHECK_SECONDS", 0.001)
    result = await check_today(7, scope(), NOW)
    assert result["status"] == "unknown" and result["reason"] == "read_timeout"


@pytest.mark.parametrize(
    "case_id,text,expected",
    [
        (
            "latest_primary_today_none",
            "No emails received today in Primary. The latest arrived yesterday at 10:09 PM AEDT.",
            [],
        ),
        (
            "latest_primary_today_none",
            "The latest arrived yesterday.",
            ["missing_verified_no_mail_today"],
        ),
        ("latest_primary_today_present", "Your latest email arrived today at 12:09 AM AEDT.", []),
        (
            "latest_primary_today_incomplete",
            "No emails received today. The latest arrived yesterday.",
            ["unverified_no_mail_today"],
        ),
        (
            "latest_primary_today_failed",
            "I could not verify today’s arrivals. The latest arrived on 6 Oct 2026.",
            [],
        ),
        (
            "latest_primary_today_empty_history",
            "No emails received today in Primary. "
            "No matching email was found in the searched date window.",
            [],
        ),
    ],
)
async def test_latest_today_replay_contract(monkeypatch, case_id, text, expected):
    from app.conversation import evaluate

    case = next(c for c in evaluate.CASES if c["id"] == case_id)
    runtime = evaluate.FixtureRuntime(case, case["turns"][0])
    result = await runtime.call(
        "search_mail", SearchMail(query="", folder="INBOX", selection="latest_message")
    )
    assert result["today_check"]["status"] == case["today_status"]
    failures = evaluate.grade(
        case, {"kind": "message", "text": text}, runtime.calls, runtime.search_page
    )
    assert failures == expected


@pytest.mark.parametrize(
    "case_id",
    [
        "latest_single_inbox_local_time",
        "latest_explicit_primary",
        "latest_all_inbox_categories",
        "latest_primary_after_local_midnight",
    ],
)
async def test_every_latest_listing_replay_rejects_default_five_result_selection(case_id):
    from app.conversation import evaluate

    case = next(c for c in evaluate.CASES if c["id"] == case_id)
    runtime = evaluate.FixtureRuntime(case, case["turns"][0])
    await runtime.call(
        "search_mail",
        SearchMail(query="", folder="INBOX", inbox_category=case["expected_inbox_category"]),
    )
    failures = evaluate.grade(
        case, {"kind": "message", "text": "I found this email."}, runtime.calls, runtime.search_page
    )
    assert "wrong_single_inbox_search" in failures
    assert "single_inbox_card_count_mismatch" in failures
