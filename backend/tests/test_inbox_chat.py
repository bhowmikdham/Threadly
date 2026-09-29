import json
from datetime import UTC, datetime

import pytest

from app.api.errors import ApiError
from app.assistant import inbox_chat as inbox
from app.schemas.inbox_chat import InboxChatRequest

NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


class Model:
    def __init__(self, **values):
        self.value = {
            "kind": "search",
            "query": "GYG",
            "date_phrase": "",
            "folder": "all_mail",
            **values,
        }
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        return json.dumps(self.value), None


@pytest.mark.parametrize("text", ["hey", "Hello!", "how are you?", "Thanks"])
async def test_social_without_model_or_mailbox(text):
    model = Model()
    result = await inbox.interpret(InboxChatRequest(instruction=text), model, NOW)
    assert result["kind"] == "message"
    assert model.calls == []


async def test_literal_search_and_timezone_calendar_month():
    result = await inbox.interpret(
        InboxChatRequest(
            instruction="Show me all GYG emails from last month", timezone="Australia/Melbourne"
        ),
        Model(date_phrase="last month"),
        NOW,
    )
    f = result["filters"]
    assert f.query == "GYG"
    assert f.received_from.isoformat() == "2026-07-31T14:00:00+00:00"
    assert f.received_before.isoformat() == "2026-08-31T14:00:00+00:00"


async def test_email_search_restores_only_masked_user_literal():
    model = Model(query="<EMAIL_1>")
    result = await inbox.interpret(
        InboxChatRequest(instruction="Find emails from alex@example.test"), model, NOW
    )
    assert "alex@example.test" not in model.calls[0]
    assert result["filters"].query == ""
    assert result["filters"].sender_email == "alex@example.test"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Did I get mail from Naveen@Example.Test?", "naveen@example.test"),
        ("Show mail sent by alice@example.test", "alice@example.test"),
        ("Show mail about alice@example.test", None),
    ],
)
def test_explicit_sender_requires_from_wording(text, expected):
    assert inbox.explicit_sender_email(text) == expected


@pytest.mark.parametrize(
    "values", [{"query": "invented"}, {"folder": "SENT"}, {"date_phrase": "last month"}]
)
async def test_invented_search_constraints_rejected(values):
    with pytest.raises(ApiError) as e:
        await inbox.interpret(InboxChatRequest(instruction="Show GYG emails"), Model(**values), NOW)
    assert e.value.code == "inbox_interpretation_invalid"


async def test_missing_explicit_dates_does_not_expand_scope():
    with pytest.raises(ApiError):
        await inbox.interpret(InboxChatRequest(instruction="GYG emails last month"), Model(), NOW)


async def test_mixed_operation_is_not_partially_executed_even_if_model_says_search():
    result = await inbox.interpret(
        InboxChatRequest(instruction="Find GYG emails and draft a reply"), Model(), NOW
    )
    assert result["kind"] == "continue"


async def test_unsupported_dates_ask_in_chat():
    result = await inbox.interpret(
        InboxChatRequest(instruction="GYG emails since the conference"),
        Model(date_phrase="since the conference"),
        NOW,
    )
    assert result["kind"] == "message" and "dates" in result["text"]


@pytest.mark.parametrize("phrase", ["past 367 days", "next spring", "past 0 days"])
def test_date_scope_is_bounded(phrase):
    with pytest.raises(ValueError):
        inbox.date_window(phrase, NOW, "UTC")


@pytest.mark.parametrize(
    "text", ["JFK to MEL", "Flight JFK to JFK", "Flight New York to Melbourne", "Flight jfk to mel"]
)
def test_flight_card_does_not_invent_codes(text):
    assert inbox.flight_preview(text) is None


def test_flight_card_keeps_exact_source_route_without_status():
    value = inbox.flight_preview("Your flight itinerary: JFK → MEL. Flight QF12")
    assert value == {
        "origin": "JFK",
        "destination": "MEL",
        "flight_number": "QF12",
        "route_quote": "JFK → MEL",
        "basis": "email_text_not_live_status",
    }


def test_committed_live_receipt_matches_current_assets():
    from pathlib import Path

    from app.planner.evaluate_inbox_chat import CASES, assets

    receipt = json.loads(
        (Path(__file__).parents[2] / "docs/evaluation/inbox-chat-live-v2.json").read_text()
    )
    for key, value in assets().items():
        assert receipt[key] == value
    assert len(receipt["cases"]) == len(CASES)
    assert all(case["passed"] for case in receipt["cases"])
