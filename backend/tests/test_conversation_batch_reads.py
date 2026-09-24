"""Batch search reads keep evidence bounded to backend-issued message references."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.assistant import inbox_chat, source_data
from app.conversation import runtime as conversation_runtime
from app.conversation.engine import validate_response
from app.conversation.runtime import SEARCH_READ_BODY_CHARS, Runtime
from app.schemas.conversation import Evidence, ReadSearchResults, Respond, SearchMail


def runtime():
    state = {
        "history": [],
        "refs": {
            "mail-1": {"message_id": "promotion", "thread_id": "thread-1"},
            "mail-2": {"message_id": "order", "thread_id": "thread-1"},
            "mail-3": {"message_id": "other", "thread_id": "thread-2"},
            "selected": {"message_id": "selected", "thread_id": "thread-3", "context_id": "owned"},
        },
        "result_order": ["mail-1", "mail-2"],
    }
    request = SimpleNamespace(instruction="Find my latest order")
    return Runtime(7, request, state, factory=None)


def message(identifier, body):
    return {
        "gmail_msg_id": identifier,
        "subject": "Order confirmation" if identifier == "order" else "Offers this weekend",
        "from_addr": "orders@example.test",
        "sent_at": "2026-09-24T00:00:00Z",
        "received_at": "2026-09-24T00:00:00Z",
        "reply_metadata": {"headers": {}},
        "body_clean": body,
    }


@pytest.mark.parametrize("references", [[], ["mail-1"] * 6, [""], ["x" * 41]])
def test_batch_schema_rejects_bad_sizes_and_references(references):
    with pytest.raises(ValidationError):
        ReadSearchResults(references=references)


@pytest.mark.parametrize("references", [["selected"], ["mail-3"], ["mail-1", "mail-1"]])
async def test_batch_rejects_unselected_or_stale_references_before_fetch(monkeypatch, references):
    async def forbidden_fetch(*_args):
        raise AssertionError("Gmail should not be queried")

    monkeypatch.setattr(source_data, "fetch", forbidden_fetch)
    subject = runtime()
    with pytest.raises(ValueError):
        await subject.call("read_search_results", ReadSearchResults(references=references))
    assert subject.evidence == {}
    assert subject.loaded == {}


async def test_batch_reads_only_named_messages_and_cites_only_returned_excerpts(monkeypatch):
    calls = []
    hidden = "HIDDEN_ORDER_NUMBER_9999"
    promo = "Special offer. " * 200 + hidden
    source = {"messages": [message("promotion", promo), message("order", "Order 2241 confirmed.")]}

    async def fetch(owner, thread_id):
        calls.append((owner, thread_id))
        return source

    monkeypatch.setattr(source_data, "fetch", fetch)
    subject = runtime()
    result = await subject.call(
        "read_search_results", ReadSearchResults(references=["mail-1", "mail-2"])
    )

    assert calls == [(7, "thread-1"), (7, "thread-1")]
    assert [row["reference"] for row in result["results"]] == ["mail-1", "mail-2"]
    assert len(result["results"][0]["messages"]) == 1
    assert len(result["results"][1]["messages"]) == 1
    excerpt = result["results"][0]["messages"][0]
    assert len(excerpt["body"]) == SEARCH_READ_BODY_CHARS
    assert excerpt["truncated"] is True
    assert hidden not in subject.evidence["mail-1"]
    assert "Order 2241 confirmed." in subject.evidence["mail-2"]
    assert subject.read_scopes == {"mail-1": {"selected_message"}, "mail-2": {"selected_message"}}
    assert result["untrusted_source"] is True

    safe = Respond(
        kind="message",
        text="I found order 2241 in the checked messages.",
        evidence=[Evidence(reference="mail-2", quote="Order 2241 confirmed.")],
    )
    assert validate_response(safe, subject)["kind"] == "message"
    invented = safe.model_copy(update={"evidence": [Evidence(reference="mail-1", quote=hidden)]})
    with pytest.raises(ValueError, match="Unverified evidence"):
        validate_response(invented, subject)


async def test_failed_batch_does_not_leave_partially_read_source(monkeypatch):
    async def fetch(_owner, thread_id):
        if thread_id == "thread-2":
            raise RuntimeError("source unavailable")
        return {"messages": [message("promotion", "Offer.")]}

    monkeypatch.setattr(source_data, "fetch", fetch)
    subject = runtime()
    subject.state["result_order"].append("mail-3")
    subject.evidence["mail-1"] = "Earlier visible evidence"
    subject.read_scopes["mail-1"] = {"selected_message"}

    with pytest.raises(RuntimeError, match="source unavailable"):
        await subject.call(
            "read_search_results", ReadSearchResults(references=["mail-1", "mail-3"])
        )

    assert subject.evidence == {"mail-1": "Earlier visible evidence"}
    assert subject.loaded == {}
    assert subject.read_scopes == {"mail-1": {"selected_message"}}


async def test_search_assigns_mail_reference_when_hit_is_also_pinned(monkeypatch):
    async def search(_owner, filters, _cursor):
        return {
            "filters": filters.model_dump(mode="json"),
            "results": [
                {
                    "message_id": "order",
                    "thread_id": "thread-1",
                    "subject": "Order confirmation",
                    "snippet": "Order 2241 confirmed",
                }
            ],
            "next_cursor": None,
            "coverage": {"complete": False},
        }

    monkeypatch.setattr(inbox_chat, "search", search)
    monkeypatch.setattr(
        conversation_runtime,
        "get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    subject = runtime()
    subject.request.timezone = "Australia/Melbourne"
    subject.request.instruction = "Find my latest order"
    subject.state["refs"]["selected"]["message_id"] = "order"

    result = await subject.search(SearchMail(query="order"))
    assert result["results"][0]["reference"] == "mail-1"
    assert subject.state["result_order"] == ["mail-1"]
    assert subject.state["refs"]["selected"]["context_id"] == "owned"
    assert subject.state["refs"]["mail-1"]["message_id"] == "order"


async def test_two_search_pages_return_all_addressable_cards_in_display_order(monkeypatch):
    async def search(_owner, filters, cursor):
        numbers = range(1, 6) if cursor is None else range(6, 7)
        return {
            "filters": filters.model_dump(mode="json"),
            "results": [
                {
                    "message_id": f"message-{number}",
                    "thread_id": f"thread-{number}",
                    "subject": f"GYG email {number}",
                }
                for number in numbers
            ],
            "next_cursor": "next-page" if cursor is None else None,
            "coverage": {"complete": False},
        }

    monkeypatch.setattr(inbox_chat, "search", search)
    monkeypatch.setattr(
        conversation_runtime,
        "get_settings",
        lambda: SimpleNamespace(gmail_source_mode="on_demand"),
    )
    subject = runtime()
    subject.request.timezone = "Australia/Melbourne"
    subject.request.instruction = "Find GYG emails"
    subject.state["refs"] = {}
    subject.state["result_order"] = []

    first = await subject.search(SearchMail(query="GYG"))
    second = await subject.search(None)
    assert [row["reference"] for row in first["results"]] == [
        f"mail-{number}" for number in range(1, 6)
    ]
    assert [row["reference"] for row in second["results"]] == ["mail-6"]
    assert [row["reference"] for row in subject.search_page["results"]] == [
        f"mail-{number}" for number in range(1, 7)
    ]
    assert subject.state["result_order"] == [
        row["reference"] for row in subject.search_page["results"]
    ]
    assert subject.search_page["next_cursor"] is None


async def test_later_batch_keeps_citation_from_earlier_full_read(monkeypatch):
    tail = "TAIL_QUOTE_FROM_EARLIER_READ"

    async def fetch(_owner, _thread_id):
        return {"messages": [message("promotion", "A" * 2100 + tail)]}

    monkeypatch.setattr(source_data, "fetch", fetch)
    subject = runtime()
    full = await subject.read("mail-1")
    assert tail in full["messages"][0]["body"]

    batch = await subject.read_search_results(["mail-1"])
    assert tail not in batch["results"][0]["messages"][0]["body"]
    answer = Respond(
        kind="message",
        text="The earlier read contains that detail.",
        evidence=[Evidence(reference="mail-1", quote=tail)],
    )
    assert validate_response(answer, subject)["kind"] == "message"
