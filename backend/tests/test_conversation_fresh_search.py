"""New mailbox scopes cannot inherit an earlier conversational search."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.api.errors import ApiError
from app.conversation import engine
from app.conversation import runtime as conversation_runtime
from app.conversation.runtime import Runtime, fresh_search_scope
from app.schemas.conversation import Respond, SearchMail


def request(instruction):
    return SimpleNamespace(instruction=instruction, timezone="Australia/Melbourne")


def old_search_state():
    return {
        "history": [{"user": "Find my latest GYG order", "assistant": "An order was found."}],
        "refs": {
            "mail-1": {"message_id": "old-order", "thread_id": "old-thread"},
            "selected": {"message_id": "pinned", "thread_id": "pinned-thread"},
        },
        "result_order": ["mail-1"],
        "search": {"next_cursor": "old-cursor", "filters": {"query": "GYG"}},
    }


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        (
            "Did I get any emails from Naveen@Example.Test?",
            {"kind": "sender", "sender_email": "naveen@example.test"},
        ),
        (
            "no like latest 2 emails in my inbox",
            {"kind": "recent_inbox", "limit": 2},
        ),
        ("What does the second one say?", None),
        ("Show me the next page of these emails", None),
        ("Find the latest 2 GYG emails in my inbox", None),
        (
            "What is the From address in the selected email? It says From: naveen@example.test",
            None,
        ),
    ],
)
def test_only_explicit_new_scopes_revoke_old_results(instruction, expected):
    assert fresh_search_scope(instruction) == expected


def test_new_scope_drops_old_mail_refs_but_keeps_pinned_source():
    state = old_search_state()
    subject = Runtime(7, request("latest 2 emails in my inbox"), state, factory=None)
    subject._reset_previous_search()
    assert state["refs"] == {"selected": {"message_id": "pinned", "thread_id": "pinned-thread"}}
    assert state["result_order"] == []
    assert "search" not in state


async def test_exact_sender_search_overrides_old_merchant_query(monkeypatch):
    observed = []

    async def search(_owner, filters, cursor):
        observed.append((filters, cursor))
        return {
            "filters": filters.model_dump(mode="json"),
            "results": [{"message_id": "sender-hit", "thread_id": "new-thread"}],
            "next_cursor": None,
            "coverage": {"complete": False},
        }

    monkeypatch.setattr(conversation_runtime.inbox_chat, "search", search)
    monkeypatch.setattr(
        conversation_runtime,
        "get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    subject = Runtime(
        7, request("Did I get any emails from naveen@example.test?"), old_search_state(), None
    )
    subject._reset_previous_search()
    result = await subject.search(SearchMail(query="GYG"))

    filters, cursor = observed[0]
    assert cursor is None
    assert filters.query == ""
    assert filters.sender_email == "naveen@example.test"
    assert filters.folder == "all_mail"
    assert result["results"][0]["reference"] == "mail-1"
    assert subject.fresh_search_attempted and subject.fresh_search_done
    assert subject.state["refs"]["mail-1"]["message_id"] == "sender-hit"


async def test_inbox_wide_latest_two_ignores_old_query_and_limits_cards(monkeypatch):
    observed = []

    async def search(_owner, filters, cursor):
        observed.append((filters, cursor))
        return {
            "filters": filters.model_dump(mode="json"),
            "results": [
                {"message_id": "newest", "thread_id": "thread-a"},
                {"message_id": "second", "thread_id": "thread-b"},
            ],
            "next_cursor": "next-page",
            "coverage": {"complete": False},
        }

    monkeypatch.setattr(conversation_runtime.inbox_chat, "search", search)
    monkeypatch.setattr(
        conversation_runtime,
        "get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    subject = Runtime(7, request("no like latest 2 emails in my inbox"), old_search_state(), None)
    subject._reset_previous_search()

    with pytest.raises(ValueError, match="Start a new search"):
        await subject.search(None)
    result = await subject.search(SearchMail(query="GYG", folder="all_mail", limit=5))

    filters, cursor = observed[0]
    assert cursor is None
    assert (filters.query, filters.sender_email, filters.folder, filters.limit) == (
        "",
        "",
        "INBOX",
        2,
    )
    assert [row["message_id"] for row in subject.search_page["results"]] == [
        "newest",
        "second",
    ]
    assert [row["reference"] for row in result["results"]] == ["mail-1", "mail-2"]


def test_model_cannot_answer_a_new_inbox_request_from_old_dialogue():
    subject = Runtime(7, request("latest 2 emails in my inbox"), old_search_state(), None)
    with pytest.raises(engine.FreshSearchRequired):
        engine.validate_response(
            Respond(kind="message", text="Your latest two messages are GYG orders."), subject
        )


async def test_failed_fresh_search_cannot_support_mailbox_facts(monkeypatch):
    async def unavailable(_owner, _filters, _cursor):
        raise ApiError(503, "gmail_unavailable", "Gmail read failed.")

    monkeypatch.setattr(conversation_runtime.inbox_chat, "search", unavailable)
    monkeypatch.setattr(
        conversation_runtime,
        "get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    subject = Runtime(7, request("latest 2 emails in my inbox"), old_search_state(), None)
    subject._reset_previous_search()

    with pytest.raises(ApiError) as failed:
        await subject.search(SearchMail(query="GYG"))

    assert failed.value.code == "gmail_unavailable"
    assert subject.fresh_search_attempted and not subject.fresh_search_done
    assert subject.search_page is None
    with pytest.raises(engine.FreshSearchRequired):
        engine.validate_response(
            Respond(kind="message", text="You have no recent messages."), subject
        )
    with pytest.raises(engine.FreshSearchRequired):
        engine.validate_response(Respond(kind="message", text="I found two GYG orders."), subject)
    assert engine.validate_response(
        Respond(kind="message", text="I couldn't check your inbox right now. Please try again."),
        subject,
    ) == {
        "kind": "message",
        "text": "I couldn't check your inbox right now. Please try again.",
        "evidence": [],
    }
    assert (
        engine.validate_response(
            Respond(kind="clarification", text="Could you give me a narrower date range?"),
            subject,
        )["kind"]
        == "clarification"
    )


async def test_disconnected_gmail_allows_truthful_unavailable_response(monkeypatch):
    monkeypatch.setattr(
        conversation_runtime,
        "get_settings",
        lambda: SimpleNamespace(gmail_source_mode="disabled"),
    )
    subject = Runtime(7, request("latest 2 emails in my inbox"), old_search_state(), None)
    subject._reset_previous_search()

    with pytest.raises(ApiError) as failed:
        await subject.search(SearchMail(query=""))

    assert failed.value.code == "live_inbox_required"
    assert subject.fresh_search_attempted and not subject.fresh_search_done
    assert (
        engine.validate_response(
            Respond(
                kind="message", text="I couldn't check your inbox right now. Please try again."
            ),
            subject,
        )["kind"]
        == "message"
    )


async def test_failed_fresh_search_budget_returns_unavailable_instead_of_old_mail():
    class RepeatedAnswer:
        count = 0

        async def decide(self, _system, _messages, _tools):
            self.count += 1
            return {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": f"reply-{self.count}",
                            "name": "respond",
                            "input": {"kind": "message", "text": "You have no recent messages."},
                        }
                    }
                ],
            }

    class FailedRuntime:
        fresh_search_scope = {"kind": "recent_inbox", "limit": 2}
        fresh_search_attempted = True
        fresh_search_done = False
        search_page = None
        evidence = {}

    result = await engine.run({}, FailedRuntime(), RepeatedAnswer())

    assert result["kind"] == "message"
    assert "couldn't check your inbox" in result["text"]
    assert len(result["trace"]) == engine.MAX_CALLS
    assert all(step["reason"] == "fresh_search_required" for step in result["trace"])


async def test_model_receives_specific_feedback_then_searches_fresh_scope():
    class Model:
        def __init__(self):
            self.messages = [
                tool("respond", kind="message", text="Those are two GYG orders."),
                tool("search_mail", query="GYG"),
                tool("respond", kind="message", text="I found two emails in this date window."),
            ]
            self.inputs = []

        async def decide(self, _system, messages, _tools):
            self.inputs.append(deepcopy(messages))
            return self.messages.pop(0)

    class SearchRuntime:
        fresh_search_scope = {"kind": "recent_inbox", "limit": 2}
        fresh_search_attempted = False
        fresh_search_done = False
        search_page = None
        evidence = {}
        state = {"result_order": []}
        request = request("latest 2 emails in my inbox")

        async def call(self, name, _arguments):
            assert name == "search_mail"
            self.fresh_search_attempted = True
            self.fresh_search_done = True
            self.search_page = {"coverage": {"complete": False}, "results": []}
            return {"results": [], "date_window": {"folder": "INBOX", "query": ""}}

    def tool(name, **values):
        return {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": name + str(values), "name": name, "input": values}}
            ],
        }

    model = Model()
    result = await engine.run({}, SearchRuntime(), model)
    assert result["trace"] == [
        {"tool": "respond", "status": "invalid", "reason": "fresh_search_required"},
        {"tool": "search_mail", "status": "ok"},
        {"tool": "respond", "status": "ok"},
    ]
    feedback = model.inputs[1][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert feedback["error"] == "fresh_search_required"
    assert "search_mail" in feedback["message"]
