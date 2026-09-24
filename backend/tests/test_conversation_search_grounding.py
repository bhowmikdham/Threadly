"""Ground recency claims to the coverage of the current bounded Gmail search."""

from copy import deepcopy

import pytest

from app.conversation import engine
from app.schemas.conversation import Respond


def respond(text, identity=None, evidence=None, kind="message"):
    return {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": identity or text[:20],
                    "name": "respond",
                    "input": {
                        "kind": kind,
                        "text": text,
                        "evidence": (
                            [{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}]
                            if evidence is None
                            else evidence
                        ),
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
        {"tool": "respond", "status": "invalid", "reason": "incomplete_search_coverage"},
        {"tool": "respond", "status": "ok"},
    ]
    feedback = model.inputs[1][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert feedback["error"] == "incomplete_search_coverage"
    assert "latest I found" in feedback["message"]


async def test_repeated_invalid_response_keeps_specific_coverage_feedback():
    unsafe = "Your latest GYG order is order 2241."
    safe = "The latest GYG order I found in this search is order 2241."
    model = Model(
        respond(unsafe, "first-overclaim"),
        respond(unsafe, "second-overclaim"),
        respond(safe, "corrected-answer"),
    )

    result = await engine.run({}, Runtime(), model)

    assert result["text"] == safe
    assert [step["status"] for step in result["trace"]] == ["invalid", "invalid", "ok"]
    for model_input in model.inputs[1:]:
        feedback = model_input[-1]["content"][0]["toolResult"]["content"][0]["json"]
        assert feedback["error"] == "incomplete_search_coverage"
        assert "latest I found" in feedback["message"]


@pytest.mark.parametrize(
    ("invalid_evidence", "expected_error"),
    [
        ([], "citation_required"),
        ([{"reference": "mail-2", "quote": "Order 9999 shipped"}], "citation_unverified"),
    ],
)
async def test_response_feedback_distinguishes_missing_and_unverified_citations(
    invalid_evidence, expected_error
):
    text = "I found order 2241 among the messages I checked."
    model = Model(
        respond(text, "first-answer", evidence=invalid_evidence),
        respond(text, "corrected-answer"),
    )

    result = await engine.run({}, Runtime(), model)

    assert result["evidence"] == [{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}]
    assert result["trace"][0] == {
        "tool": "respond",
        "status": "invalid",
        "reason": expected_error,
    }
    feedback = model.inputs[1][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert feedback["error"] == expected_error
    assert "read_search_results" in feedback["message"]


async def test_no_confirmed_order_can_ask_for_narrower_search_without_a_citation():
    model = Model(
        respond("I could not verify an order in these results.", "uncited", evidence=[]),
        respond(
            "Can you narrow the date or sender?",
            "narrower-search",
            evidence=[],
            kind="clarification",
        ),
    )

    result = await engine.run({}, Runtime(), model)

    assert result["kind"] == "clarification"
    assert result["evidence"] == []
    feedback = model.inputs[1][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert feedback["error"] == "citation_required"
    assert "kind=clarification" in feedback["message"]


async def test_clarification_cannot_assert_uncited_order_rank():
    unsafe = "Order 2241 is the most recent among the results I checked. Can you narrow the date?"
    model = Model(
        respond(unsafe, "ranking-clarification", evidence=[], kind="clarification"),
        respond(
            "Can you narrow the date or sender?",
            "safe-clarification",
            evidence=[],
            kind="clarification",
        ),
    )

    result = await engine.run({}, Runtime(), model)

    assert result["text"] == "Can you narrow the date or sender?"
    assert result["trace"][0] == {
        "tool": "respond",
        "status": "invalid",
        "reason": "clarification_claims_source_fact",
    }
    assert engine._asserts_inbox_rank("What is your latest order?") is False


@pytest.mark.parametrize(
    "unsupported",
    [
        "I found no confirmed GYG orders. Can you narrow the date?",
        "Order 2241 was confirmed. Which date should I check?",
        "Please note that order 2241 is confirmed.",
        "Please clarify: order 3344 is the latest GYG order.",
        "Please clarify order 3344 is latest.",
    ],
)
def test_uncited_clarification_cannot_assert_mailbox_findings(unsupported):
    with pytest.raises(engine.UnsupportedClarificationClaim):
        engine.validate_response(
            Respond(kind="clarification", text=unsupported, evidence=[]), Runtime()
        )


@pytest.mark.parametrize(
    "question",
    [
        "Can you narrow the date or sender?",
        "Which order did you mean?",
        "Please select an accessible thread.",
    ],
)
def test_uncited_clarification_can_request_missing_details(question):
    result = engine.validate_response(
        Respond(kind="clarification", text=question, evidence=[]), Runtime()
    )
    assert result["text"] == question


@pytest.mark.parametrize(
    ("instruction", "result_order", "claim"),
    [
        ("Find my latest GYG order", [], "Your latest GYG order is 2241."),
        ("Find my latest GYG order", [], "Looks like 2241 is newest."),
        (
            "Is that my latest GYG order?",
            ["mail-1", "mail-2"],
            "Order 2241 is your latest GYG order.",
        ),
        (
            "Is that my latest GYG order?",
            ["mail-1", "mail-2"],
            "The latest GYG order I found in this search is 2241.",
        ),
        (
            "Is that my latest GYG order?",
            ["mail-1", "mail-2"],
            "Order 2241 is latest.",
        ),
        (
            "Is that my latest GYG order?",
            ["mail-1", "mail-2"],
            "Looks like 2241 is newest.",
        ),
    ],
)
async def test_unread_inbox_rank_requires_citation_with_or_without_retained_results(
    instruction, result_order, claim
):
    runtime = Runtime(current=False, instruction=instruction)
    runtime.evidence = {}
    runtime.state["result_order"] = result_order
    model = Model(
        respond(claim, "unsupported-rank", evidence=[]),
        respond(
            "Could you give me a date or sender to narrow the search?",
            "clarifying-question",
            evidence=[],
            kind="clarification",
        ),
    )

    result = await engine.run({}, runtime, model)

    assert result["kind"] == "clarification"
    assert result["trace"][0] == {
        "tool": "respond",
        "status": "invalid",
        "reason": "citation_required",
    }
    feedback = model.inputs[1][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert feedback["error"] == "citation_required"
    assert "Read the relevant email" in feedback["message"]
    assert "You read source text" not in feedback["message"]


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("message", "Hey! How can I help?"),
        ("clarification", "What is your latest order?"),
        ("clarification", "Please clarify whether order 2241 is your latest."),
    ],
)
def test_source_free_greeting_and_direct_clarification_remain_available(kind, text):
    runtime = Runtime(current=False)
    runtime.evidence = {}

    assert (
        engine.validate_response(Respond(kind=kind, text=text, evidence=[]), runtime)["text"]
        == text
    )


async def test_latest_answer_inspects_newer_returned_cards_before_ranking_older_order():
    class RankedRuntime(Runtime):
        def __init__(self):
            super().__init__()
            self.search_page = {
                "results": [
                    {"reference": "mail-1", "received_at": "2026-09-23T10:00:00Z"},
                    {"reference": "mail-2", "received_at": "2026-09-20T10:00:00Z"},
                ],
                "coverage": {"complete": False},
            }

        async def call(self, name, arguments):
            assert name == "read_search_results"
            assert arguments.references == ["mail-1"]
            self.evidence["mail-1"] = "Purchase 3344 was confirmed."
            return {
                "results": [
                    {
                        "reference": "mail-1",
                        "messages": [{"body": "Purchase 3344 was confirmed."}],
                    }
                ]
            }

    model = Model(
        respond("Order 2241 is the most recent I found in this search.", "older"),
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "read-newer",
                        "name": "read_search_results",
                        "input": {"references": ["mail-1"]},
                    }
                }
            ],
        },
        respond(
            "I found confirmed purchase 3344 among the results I checked.",
            "newer",
            evidence=[{"reference": "mail-1", "quote": "Purchase 3344 was confirmed."}],
        ),
    )

    result = await engine.run({}, RankedRuntime(), model)

    assert result["text"].startswith("I found confirmed purchase 3344")
    assert result["trace"] == [
        {"tool": "respond", "status": "invalid", "reason": "uninspected_newer_results"},
        {"tool": "read_search_results", "status": "ok"},
        {"tool": "respond", "status": "ok"},
    ]
    feedback = model.inputs[1][-1]["content"][0]["toolResult"]["content"][0]["json"]
    assert feedback["error"] == "uninspected_newer_results"
    assert "mail-1" in feedback["message"]


def test_newer_read_promotion_cannot_hide_unread_intermediate_card():
    runtime = Runtime()
    runtime.search_page = {
        "results": [
            {"reference": "mail-1", "received_at": "2026-09-25T10:00:00Z"},
            {"reference": "mail-2", "received_at": "2026-09-23T10:00:00Z"},
            {"reference": "mail-3", "received_at": "2026-09-20T10:00:00Z"},
        ],
        "coverage": {"complete": False},
    }
    runtime.evidence = {
        "mail-1": "GYG rewards are available.",
        "mail-3": "GYG order 2241 confirmed.",
    }
    answer = Respond(
        kind="message",
        text="The latest GYG order I found in this search is 2241.",
        evidence=[
            {"reference": "mail-1", "quote": "GYG rewards are available."},
            {"reference": "mail-3", "quote": "GYG order 2241 confirmed."},
        ],
    )

    with pytest.raises(engine.UninspectedNewerResults) as error:
        engine.validate_response(answer, runtime)

    assert error.value.references == ["mail-2"]


def test_latest_followup_uses_retained_result_order_when_cards_are_transient():
    runtime = Runtime(current=False, instruction="Which one is it?")
    runtime.state["result_order"] = ["mail-1", "mail-2"]
    answer = Respond(
        kind="message",
        text="The latest GYG order I found in this search is 2241.",
        evidence=[{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}],
    )

    with pytest.raises(engine.UninspectedNewerResults) as error:
        engine.validate_response(answer, runtime)

    assert error.value.references == ["mail-1"]


@pytest.mark.parametrize(
    "answer_text",
    [
        "Order 2241 appears to be the newest I found.",
        "It looks like 2241 is newest.",
    ],
)
def test_followup_tentative_rank_also_requires_newer_card_inspection(answer_text):
    runtime = Runtime(current=False, instruction="Which one is it?")
    runtime.state["result_order"] = ["mail-1", "mail-2"]
    answer = Respond(
        kind="message",
        text=answer_text,
        evidence=[{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}],
    )

    with pytest.raises(engine.UninspectedNewerResults) as error:
        engine.validate_response(answer, runtime)
    assert error.value.references == ["mail-1"]


@pytest.mark.parametrize(
    "answer_text",
    [
        "I found order 2241 among the results I checked; newer cards remain unchecked.",
        "Order 2241 appears to be the newest I found.",
    ],
)
def test_latest_request_checks_newer_cards_even_without_a_formal_rank(answer_text):
    runtime = Runtime()
    runtime.search_page = {
        "results": [
            {"reference": "mail-1", "received_at": "2026-09-23T10:00:00Z"},
            {"reference": "mail-2", "received_at": "2026-09-20T10:00:00Z"},
        ],
        "coverage": {"complete": False},
    }
    answer = Respond(
        kind="message",
        text=answer_text,
        evidence=[{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}],
    )

    with pytest.raises(engine.UninspectedNewerResults) as error:
        engine.validate_response(answer, runtime)
    assert error.value.references == ["mail-1"]


def test_unranked_answer_without_latest_request_does_not_force_unrelated_reads():
    runtime = Runtime(instruction="Find a GYG order")
    runtime.search_page = {
        "results": [
            {"reference": "mail-1", "received_at": "2026-09-23T10:00:00Z"},
            {"reference": "mail-2", "received_at": "2026-09-20T10:00:00Z"},
        ],
        "coverage": {"complete": False},
    }
    answer = Respond(
        kind="message",
        text="I found order 2241 among the results I checked.",
        evidence=[{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}],
    )

    assert engine.validate_response(answer, runtime)["text"] == answer.text


async def test_budget_fallback_retains_verified_quote_without_claiming_global_latest():
    class SearchReadRuntime:
        def __init__(self):
            self.evidence = {}
            self.search_page = None
            self.calls = []

        async def call(self, name, arguments):
            self.calls.append(name)
            if name == "search_mail":
                self.search_page = {
                    "results": [{"reference": "mail-2"}],
                    "coverage": {"complete": False},
                }
                return {"results": [{"reference": "mail-2"}], "coverage": {"complete": False}}
            if name == "read_email":
                assert arguments.reference == "mail-2"
                self.evidence["mail-2"] = "GYG order 2241 confirmed"
                return {"reference": "mail-2", "messages": [{"body": self.evidence["mail-2"]}]}
            raise AssertionError("Only a bounded search and read are expected")

    unsafe = "Your latest GYG order is order 2241."
    model = Model(
        search({"query": "GYG"}, "search"),
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "read-order",
                        "name": "read_email",
                        "input": {"reference": "mail-2"},
                    }
                }
            ],
        },
        *(respond(unsafe, f"invalid-response-{number}") for number in range(6)),
    )
    runtime = SearchReadRuntime()

    result = await engine.run({"user_turn": "Find my latest GYG order"}, runtime, model)

    assert runtime.calls == ["search_mail", "read_email"]
    assert len(result["trace"]) == engine.MAX_CALLS
    assert all(step["status"] == "invalid" for step in result["trace"][2:])
    assert result["kind"] == "message"
    assert engine.overclaims_incomplete_search(result["text"]) is False
    assert result["evidence"] == [{"reference": "mail-2", "quote": "GYG order 2241 confirmed"}]


async def test_search_fallback_does_not_cite_unrelated_pinned_email():
    class PinnedReadRuntime:
        def __init__(self):
            self.evidence = {}
            self.search_page = None

        async def call(self, name, arguments):
            if name == "search_mail":
                self.search_page = {
                    "results": [{"reference": "mail-1"}],
                    "coverage": {"complete": False},
                }
                return self.search_page
            if name == "read_email":
                self.evidence["selected"] = "Private unrelated source"
                return {
                    "reference": "selected",
                    "messages": [{"body": self.evidence["selected"]}],
                }
            raise AssertionError("Unexpected tool")

    model = Model(
        search({"query": "GYG"}, "search"),
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "read-pinned",
                        "name": "read_email",
                        "input": {"reference": "selected"},
                    }
                }
            ],
        },
        *(
            respond(
                "Your latest GYG order is 3344.",
                f"invalid-{number}",
                evidence=[{"reference": "selected", "quote": "Private unrelated source"}],
            )
            for number in range(6)
        ),
    )

    result = await engine.run({}, PinnedReadRuntime(), model)

    assert len(result["trace"]) == engine.MAX_CALLS
    assert result["evidence"] == []
    assert result["text"].startswith("I found matching emails but couldn't finish")


async def test_new_search_clears_verified_quote_before_budget_fallback():
    class SearchResetRuntime:
        def __init__(self):
            self.evidence = {}
            self.search_page = None
            self.searches = 0

        async def call(self, name, arguments):
            if name == "search_mail":
                self.searches += 1
                self.evidence.clear()
                self.search_page = {
                    "results": [{"reference": "mail-2" if self.searches == 1 else "mail-1"}],
                    "coverage": {"complete": False},
                }
                return self.search_page
            if name == "read_email":
                self.evidence["mail-2"] = "GYG order 2241 confirmed"
                return {"reference": "mail-2", "messages": [{"body": self.evidence["mail-2"]}]}
            raise AssertionError("Unexpected tool")

    unsafe = "Your latest GYG order is order 2241."
    model = Model(
        search({"query": "GYG"}, "first-search"),
        {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "read-first-result",
                        "name": "read_email",
                        "input": {"reference": "mail-2"},
                    }
                }
            ],
        },
        respond(unsafe, "first-overclaim"),
        search({"query": "order"}, "second-search"),
        *(respond(unsafe, f"stale-response-{number}") for number in range(4)),
    )

    result = await engine.run({}, SearchResetRuntime(), model)

    assert len(result["trace"]) == engine.MAX_CALLS
    assert result["evidence"] == []
    assert result["text"].startswith("I found matching emails but couldn't finish")


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
        ("Order 3344 is your most recent.", "Find my latest GYG order", True),
        ("Order 3344 is definitely your latest.", "Find my latest GYG order", True),
        (
            "Among these search results, order 3344 is your most recent.",
            "Find my latest GYG order",
            False,
        ),
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
