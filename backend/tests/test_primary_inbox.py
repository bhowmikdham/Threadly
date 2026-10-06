"""Primary uses Gmail's category query; date language uses the recipient's day."""

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.api.errors import ApiError
from app.assistant import inbox_chat, source_data
from app.conversation import evaluate
from app.conversation.runtime import Runtime
from app.mail import live
from app.mail.presentation import clock_context, received_display
from app.schemas.conversation import ConversationTurn, Evidence, SearchMail
from app.schemas.inbox_chat import InboxChatRequest, InboxFilters

ZONE = "Australia/Melbourne"
NOW = datetime(2026, 10, 6, 13, 20, tzinfo=UTC)  # 7 Oct, 00:20 AEDT


def filters(**changes):
    return InboxFilters(
        schema_version="1.0",
        received_from=NOW - timedelta(days=1),
        received_before=NOW,
        **{"folder": "INBOX", "timezone": ZONE, "limit": 1, **changes},
    )


def request(text="Show the latest mail in my inbox"):
    return ConversationTurn(
        conversation_id=str(uuid4()),
        request_id=str(uuid4()),
        expected_version=0,
        instruction=text,
        timezone=ZONE,
    )


@pytest.fixture
def gmail(monkeypatch):
    calls = []
    rows = {
        "a1": ("Promotion", "2026-10-06T11:26:00Z", ["INBOX", "CATEGORY_PROMOTIONS"]),
        "a2": ("Account notice", "2026-10-06T11:09:00Z", ["INBOX", "CATEGORY_UPDATES"]),
        "a3": ("Older notice", "2026-10-06T10:30:00Z", ["INBOX"]),
        "a4": ("Archived notice", "2026-10-06T12:00:00Z", ["CATEGORY_PERSONAL"]),
    }

    async def account(_owner):
        return "synthetic-token", 1, "owner@example.test"

    async def check(_owner, _version):
        return None

    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/messages"):
            primary = "category:primary" in req.url.params["q"]
            size = int(req.url.params["maxResults"])
            if req.url.params.get("pageToken") == "next-primary":
                ids, token = ["a3"], None
            elif primary:
                ids, token = ["a2", "a4"][:size], "next-primary"
            else:
                ids, token = ["a1"], None
            return httpx.Response(
                200,
                json={
                    "messages": [{"id": mid, "threadId": mid} for mid in ids],
                    **({"nextPageToken": token} if token else {}),
                },
            )
        mid = req.url.path.rsplit("/", 1)[-1]
        subject, received, labels = rows[mid]
        return httpx.Response(
            200,
            json={
                "id": mid,
                "threadId": mid,
                "labelIds": labels,
                "internalDate": str(int(datetime.fromisoformat(received).timestamp() * 1000)),
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": subject},
                        {"name": "From", "value": "Sender <sender@example.test>"},
                    ],
                    "body": {"data": base64.urlsafe_b64encode(b"Synthetic notice.").decode()},
                },
            },
        )

    original = live.search_page

    async def search_page(owner, query, **kwargs):
        return await original(owner, query, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(live, "account", account)
    monkeypatch.setattr(live, "check_account", check)
    monkeypatch.setattr(live, "search_page", search_page)
    return calls


async def test_primary_query_omits_promotion_keeps_unlabelled_primary_and_pages(gmail):
    scope = filters(inbox_category="primary")
    first = await inbox_chat.search(7, scope)
    assert [r["subject"] for r in first["results"]] == ["Account notice"]
    assert first["filters"]["inbox_category"] == "primary"
    assert first["results"][0]["message_id"] == "a2"  # CATEGORY_UPDATES can match Primary.
    assert "in:inbox category:primary" in gmail[0].url.params["q"]
    second = await inbox_chat.search(7, scope, first["next_cursor"])
    assert second["results"][0]["message_id"] == "a3"  # Uncategorised Primary result retained.
    assert second["next_cursor"] is None


async def test_primary_rechecks_inbox_and_fills_after_archived_provider_hit(gmail):
    result = await inbox_chat.search(7, filters(inbox_category="primary", limit=2))
    assert [r["message_id"] for r in result["results"]] == ["a2", "a3"]
    assert result["coverage"]["provider_pages_read"] == 2


async def test_all_categories_and_legacy_public_default_retain_promotion(gmail):
    for scope in (filters(), filters(inbox_category="all")):
        result = await inbox_chat.search(7, scope)
        assert result["results"][0]["subject"] == "Promotion"
        assert result["filters"]["inbox_category"] == "all"
    queries = [r.url.params["q"] for r in gmail if r.url.path.endswith("/messages")]
    assert all("in:inbox" in q and "category:" not in q for q in queries)


async def test_category_change_invalidates_cursor_before_provider_read(gmail):
    first = await inbox_chat.search(7, filters(inbox_category="primary"))
    count = len(gmail)
    with pytest.raises(ApiError) as error:
        await inbox_chat.search(7, filters(inbox_category="all"), first["next_cursor"])
    assert error.value.code == "mail_search_cursor_invalid"
    assert len(gmail) == count


@pytest.mark.parametrize("folder", ["all_mail", "SENT"])
def test_public_primary_scope_requires_inbox(folder):
    with pytest.raises(ValidationError, match="Primary category requires"):
        filters(folder=folder, inbox_category="primary")


@pytest.mark.parametrize("category", ["primary", "all"])
async def test_runtime_defaults_primary_and_preserves_explicit_category_across_pages(
    monkeypatch, category
):
    seen = []

    async def search(owner, scope, cursor):
        seen.append((scope, cursor))
        return {
            "filters": scope.model_dump(mode="json"),
            "results": [],
            "next_cursor": "next" if cursor is None else None,
            "coverage": {"complete": False},
        }

    monkeypatch.setattr(inbox_chat, "search", search)
    monkeypatch.setattr(
        "app.conversation.runtime.get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    subject = Runtime(7, request(), {"history": [], "refs": {}, "result_order": []}, None)
    args = SearchMail(
        query="",
        folder="INBOX",
        selection="latest_message",
        **({"inbox_category": category} if category == "all" else {}),
    )
    await subject.search(args)
    await subject.search(None)
    assert [(f.inbox_category, f.limit, cursor) for f, cursor in seen] == [
        (category, 1, None),
        (category, 1, None),  # Independent local-day check; original cursor is retained.
        (category, 1, "next"),
    ]


async def test_primary_only_wording_is_folder_scope_not_literal_query(monkeypatch):
    seen = []

    async def search(owner, scope, cursor):
        seen.append(scope)
        return {
            "filters": scope.model_dump(mode="json"),
            "results": [],
            "next_cursor": None,
            "coverage": {"complete": False},
        }

    monkeypatch.setattr(inbox_chat, "search", search)
    monkeypatch.setattr(
        "app.conversation.runtime.get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    subject = Runtime(
        7, request("Check my Primary"), {"history": [], "refs": {}, "result_order": []}, None
    )
    result = await subject.search(SearchMail(query="", folder="INBOX"))
    assert (seen[0].query, seen[0].folder, seen[0].inbox_category) == ("", "INBOX", "primary")
    assert result["results"] == [] and result["has_more"] is False


@pytest.mark.parametrize("category", ["primary", "all"])
async def test_legacy_interpreter_preserves_typed_primary_or_all_scope(category):
    class Model:
        async def generate(self, prompt, **kwargs):
            return json.dumps(
                {
                    "kind": "search",
                    "query": "",
                    "date_phrase": "",
                    "folder": "INBOX",
                    "inbox_category": category,
                }
            ), None

    instruction = "Show my Primary inbox" if category == "primary" else "Show all inbox categories"
    result = await inbox_chat.interpret(
        InboxChatRequest(instruction=instruction, timezone=ZONE), Model(), NOW
    )
    assert result["filters"].inbox_category == category


@pytest.mark.parametrize(
    "instant,anchor,relation",
    [
        ("2026-10-06T11:09:00Z", NOW, "yesterday"),
        ("2026-10-06T13:09:00Z", NOW, "today"),
        ("2026-10-05T11:09:00Z", NOW, "absolute"),
        ("2026-10-03T15:59:00Z", datetime(2026, 10, 4, 13, 5, tzinfo=UTC), "yesterday"),
        ("2026-04-04T16:01:00Z", datetime(2026, 4, 5, 14, 5, tzinfo=UTC), "yesterday"),
    ],
)
def test_relative_day_uses_local_calendar_date_across_midnight_and_dst(instant, anchor, relation):
    assert received_display(instant, ZONE, reference_at=anchor)["received_day_relation"] == relation


def test_clock_context_has_current_local_date_even_when_utc_is_previous_day():
    context = clock_context(NOW, ZONE)
    assert context["now"] == "2026-10-06T13:20:00+00:00"
    assert context["now_local"] == "2026-10-07T00:20:00+11:00"
    assert context["current_date_local"] == "2026-10-07"


@pytest.mark.parametrize("quote", [" \n\t", "\u200b\u200c\u200d", "&amp;zwnj; &nbsp;", "\ufeff"])
def test_invisible_citation_quotes_are_rejected_without_rewriting_source(quote):
    with pytest.raises(ValidationError, match="visible source excerpt"):
        Evidence(reference="mail-1", quote=quote)


@pytest.mark.parametrize(
    "quote", ["$19.95", "https://example.test/receipt", "—", "می\u200cخواهم", "👩\u200d💻"]
)
def test_meaningful_citation_values_remain_exact(quote):
    assert Evidence(reference="mail-1", quote=quote).quote == quote


async def test_current_clock_and_relative_day_reach_search_read_and_batch_after_midnight(
    monkeypatch,
):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args):
            return SimpleNamespace()

    row = {
        "message_id": "a2",
        "thread_id": "a2",
        "subject": "Account notice",
        "received_at": "2026-10-06T11:09:00Z",
    }

    async def search(owner, scope, cursor):
        return {
            "filters": scope.model_dump(mode="json"),
            "results": [dict(row)],
            "next_cursor": None,
            "coverage": {"complete": False},
        }

    async def fetch(owner, tid):
        return {
            "messages": [
                {
                    "gmail_msg_id": "a2",
                    "gmail_thread_id": "a2",
                    "subject": row["subject"],
                    "from_addr": "sender@example.test",
                    "sent_at": row["received_at"],
                    "received_at": row["received_at"],
                    "body_clean": "This is a meaningful account notice.",
                    "reply_metadata": {"headers": {}},
                }
            ]
        }

    monkeypatch.setattr(inbox_chat, "search", search)
    monkeypatch.setattr(source_data, "fetch", fetch)
    monkeypatch.setattr(
        "app.conversation.runtime.get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    monkeypatch.setattr(
        "app.conversation.runtime.build_capabilities", lambda user: {"capabilities": []}
    )
    subject = Runtime(
        7,
        request(),
        {
            "history": [],
            "refs": {},
            "result_order": [],
            "calendar_read_anchor": "2026-10-06T12:00:00Z",
        },
        Session,
    )
    subject.mail_anchor = NOW
    context = await subject.context()
    assert context["current_date_local"] == "2026-10-07"
    assert subject.calendar_anchor.date().isoformat() == "2026-10-06"
    searched = await subject.search(
        SearchMail(query="", folder="INBOX", selection="latest_message")
    )
    read = await subject.read("mail-1")
    batch = await subject.read_search_results(["mail-1"])
    for message in (
        searched["results"][0],
        read["messages"][0],
        batch["results"][0]["messages"][0],
    ):
        assert message["received_day_relation"] == "yesterday"
        assert message["received_at"] == row["received_at"]
        assert message["received_at_local"] == "2026-10-06T22:09:00+11:00"
        assert message["received_display_reference_at"] == NOW.isoformat()
    assert "This is a meaningful account notice." in subject.evidence["mail-1"]


async def test_primary_search_rejects_legacy_replica_mode_before_read(monkeypatch):
    monkeypatch.setattr(
        "app.conversation.runtime.get_settings",
        lambda: SimpleNamespace(gmail_source_mode="legacy_sync"),
    )
    subject = Runtime(7, request(), {"history": [], "refs": {}, "result_order": []}, None)
    with pytest.raises(ApiError) as error:
        await subject.search(SearchMail(query="", folder="INBOX"))
    assert error.value.code == "live_inbox_required"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("It arrived today.", ["mail_relative_day_mismatch"]),
        ("It arrived yesterday.", []),
        ("It arrived on 6 Oct 2026.", []),
    ],
)
def test_evaluator_rejects_utc_date_as_today_after_melbourne_midnight(text, expected):
    assert (
        evaluate._mail_display_failures(
            text, {"received_at": "2026-10-06T11:09:00Z"}, reference_at=NOW
        )
        == expected
    )


@pytest.mark.parametrize(
    "case_id,category",
    [
        ("latest_single_inbox_local_time", "primary"),
        ("latest_explicit_primary", "primary"),
        ("latest_all_inbox_categories", "all"),
        ("latest_primary_after_local_midnight", "primary"),
    ],
)
async def test_semantic_category_replay_contract_rejects_mismatched_scope(case_id, category):
    case = next(c for c in evaluate.CASES if c["id"] == case_id)
    runtime = evaluate.FixtureRuntime(case, case["turns"][0])
    await runtime.call(
        "search_mail",
        SearchMail(query="", folder="INBOX", selection="latest_message", inbox_category=category),
    )
    answer = {"kind": "message", "text": "I found this email."}
    assert evaluate.grade(case, answer, runtime.calls, runtime.search_page) == []
    runtime.search_page["filters"]["inbox_category"] = "all" if category == "primary" else "primary"
    assert "inbox_category_scope_mismatch" in evaluate.grade(
        case, answer, runtime.calls, runtime.search_page
    )
