"""Ground recency claims to the coverage of the current bounded Gmail search."""

from copy import deepcopy

import pytest

from app.conversation import engine
from app.schemas.conversation import Respond


def respond(text):
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": text[:20],
                    "name": "respond",
                    "input": {
                        "kind": "message",
                        "text": text,
                        "evidence": [{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}],
                    },
                }
            }
        ],
    }


def search(values, identity):
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": identity,
                    "name": "search_mail",
                    "input": values,
                }
            }
        ],
    }


class Model:
    def __init__(self, *messages):
        self.messages = list(messages)
        self.inputs = []

    async def decide(self, system, messages, tools):
        self.inputs.append(deepcopy(messages))
        return self.messages.pop(0)


class Runtime:
    def __init__(self, complete=False, current=True, instruction=None):
        self.evidence = {"mail-2": "GYG order 2241 confirmed"}
        self.search_page = {"coverage": {"complete": complete}} if current else None
        self.state = {
            "result_order": ["mail-2"],
            "search": {"coverage": {"complete": complete}},
        }
        self.instruction = instruction or "Can you fetch my latest GYG order?"

    async def call(self, name, arguments):
        raise AssertionError("No observation tool should be called")


async def test_equivalent_search_defaults_do_not_issue_two_provider_searches():
    class SearchRuntime:
        evidence = {}
        search_page = None
        instruction = "Find GYG"

        def __init__(self):
            self.calls = []

        async def call(self, name, arguments):
            self.calls.append((name, arguments.model_dump()))
            return {"results": []}

    model = Model(
        search({"query": "GYG"}, "search-one"),
        search({"query": "GYG", "date_phrase": "", "folder": "all_mail"}, "search-two"),
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "answer",
                        "name": "respond",
                        "input": {"kind": "message", "text": "I found no matches.", "evidence": []},
                    }
                }
            ],
        },
    )
    runtime = SearchRuntime()

    result = await engine.run({}, runtime, model)

    assert runtime.calls == [
        ("search_mail", {"query": "GYG", "date_phrase": "", "folder": "all_mail"})
    ]
    assert result["trace"] == [
        {"tool": "search_mail", "status": "ok"},
        {"tool": "search_mail", "status": "invalid"},
        {"tool": "respond", "status": "ok"},
    ]


async def test_incomplete_search_overclaim_is_rejected_then_corrected():
    unsafe = "Your latest GYG order is order 2241."
    safe = "The latest GYG order I found in this search is order 2241."
    model = Model(respond(unsafe), respond(safe))

    result = await engine.run({}, Runtime(), model)

    assert result["text"] == safe
    assert result["trace"] == [
        {"tool": "respond", "status": "invalid"},
        {"tool": "respond", "status": "ok"},
    ]
    feedback = model.inputs[1][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert feedback["error"] == "incomplete_search_coverage"
    assert "latest I found" in feedback["message"]


@pytest.mark.parametrize(
    ("text", "user_turn", "overclaim"),
    [
        ("Your latest GYG order is 2241.", "Find my latest GYG order", True),
        (
            "The most recent order I found in this date window is 2241.",
            "Find my most recent GYG order",
            False,
        ),
        (
            "Among these results, your newest order is 2241.",
            "Find my newest GYG order",
            False,
        ),
        (
            "Of the emails I checked, the newest order is 2241.",
            "Find my newest GYG order",
            False,
        ),
        (
            "I found your latest GYG order: 2241.",
            "Find my latest GYG order",
            True,
        ),
        (
            "Your latest GYG order is 2241. I found a promotion too.",
            "Find my latest GYG order",
            True,
        ),
        ("Latest GYG order: 2241.", "Find my latest GYG order", True),
        ("Order 2241 arrived on Tuesday.", "Find my latest GYG order", False),
        (
            "The email promotes the latest menu.",
            "What does this email say?",
            False,
        ),
    ],
)
def test_recency_claim_detection_is_bounded_to_search_scope(text, user_turn, overclaim):
    assert engine.overclaims_incomplete_search(text, user_turn) is overclaim


def test_complete_search_can_make_absolute_recency_claim():
    result = engine.validate_response(
        Respond(
            text="Your latest GYG order is order 2241.",
            kind="message",
            evidence=[{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}],
        ),
        Runtime(complete=True),
    )
    assert result["text"] == "Your latest GYG order is order 2241."


def test_followup_read_keeps_persisted_incomplete_search_coverage():
    answer = Respond(
        text="Your latest GYG order is order 2241.",
        kind="message",
        evidence=[{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}],
    )
    with pytest.raises(engine.IncompleteSearchCoverage):
        engine.validate_response(
            answer,
            Runtime(current=False, instruction="What did you find?"),
        )


def test_retained_search_does_not_reclassify_a_fact_inside_one_email():
    runtime = Runtime(
        current=False,
        instruction="What is the latest menu mentioned in that email?",
    )
    runtime.evidence["mail-2"] = "The email says the spring menu is the latest."
    answer = Respond(
        text="The email says the spring menu is the latest.",
        kind="message",
        evidence=[
            {"reference": "mail-2", "quote": "The email says the spring menu is the latest."}
        ],
    )

    result = engine.validate_response(answer, runtime)

    assert result["text"] == "The email says the spring menu is the latest."
