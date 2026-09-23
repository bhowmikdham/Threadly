"""Replay the real frontend reference case without private email fixtures."""

import copy

import pytest

from app.assistant import ui_routing
from app.assistant.summary import digest


def snapshot():
    messages = [
        {"message_id": "m1", "body": "Order total: $18.60.", "sender": "Supplier"},
        {"message_id": "m2", "body": "Other order total: $99.00.", "sender": "Supplier"},
    ]
    return {
        "messages": messages,
        "total_synced_messages": 2,
        "truncated_message_ids": [],
        "ui_map": {
            "schema_version": "1.0",
            "surface": "gmail_thread",
            "thread_version": 1,
            "captured_at": "2026-09-23T00:00:00Z",
            "visible_message_ids": ["m2", "m1"],
            "selected_message_ids": ["m1"],
        },
    }


@pytest.mark.parametrize(
    "text,target",
    [
        ("What is the order total in this email?", "m1"),
        ("How much did I pay in the second message?", "m1"),
        ("When is pickup in the first email?", "m2"),
        ("Who sent this message?", "m1"),
    ],
)
def test_factual_reference_scopes_grounded_answer(text, target):
    source = snapshot()
    binding = ui_routing.bind_reference(text, source)
    assert binding["mode"] == "answer"
    assert binding["message_id"] == target
    route = ui_routing.reference_route(binding, "context")
    assert route["decision"]["operations"] == ["lookup_entity"]
    assert route["decision"]["requested_action"] == "none"
    assert ui_routing.validate_binding(route, text, "context", source) == binding
    scoped = ui_routing.scoped_snapshot(source, binding)
    assert [m["message_id"] for m in scoped["messages"]] == [target]
    assert digest(scoped["messages"][0]) == binding["source_version"]


@pytest.mark.parametrize(
    "text",
    [
        "What is the total in this email and send a reply?",
        "What is the total in this email; book a meeting.",
        "What is the total in this email? Also draft a reply.",
        "What is in this email and what is in the first message?",
        "Reply to this email.",
    ],
)
def test_compound_and_write_requests_are_not_silently_answered(text):
    binding = ui_routing.bind_reference(text, snapshot())
    assert binding["status"] != "ready" or binding.get("mode") != "answer"


def test_ambiguous_selection_and_changed_sources_still_fail_closed():
    source = snapshot()
    source["ui_map"]["selected_message_ids"] = ["m1", "m2"]
    assert (
        ui_routing.bind_reference("What is the total in this email?", source)["status"]
        == "needs_clarification"
    )
    source = snapshot()
    binding = ui_routing.bind_reference("What is the total in this email?", source)
    changed = copy.deepcopy(source)
    changed["messages"][0]["body"] = "Changed"
    with pytest.raises(ValueError):
        ui_routing.scoped_snapshot(changed, binding)
