from orch_planner import parse_merchant, parse_window_days, plan
from threadly_common.models import Intent


def test_gyg_order_lookup_is_fetch_entity():
    p = plan("give me the order number of all the orders that i did for GYG")
    assert p.intent == Intent.FETCH_ENTITY
    assert p.params["type"] == "order"
    assert p.params["merchant"] == "GYG"


def test_window_parsing():
    p = plan("show my orders from the last 2 months")
    assert p.intent == Intent.FETCH_ENTITY
    assert p.params["window_days"] == 60
    assert parse_window_days("last 3 weeks") == 21
    assert parse_window_days("no window here") is None


def test_summarise():
    assert plan("summarise this thread").intent == Intent.SUMMARISE
    assert plan("tl;dr please").intent == Intent.SUMMARISE
    assert plan("can you catch me up on this?").intent == Intent.SUMMARISE


def test_draft_is_imperative():
    assert plan("draft a polite reply to Priya").intent == Intent.DRAFT
    assert plan("reply saying I'll be there").intent == Intent.DRAFT
    assert plan("compose an email to sam about coffee").intent == Intent.DRAFT
    # question mentioning 'write' must not become a draft
    assert plan("what did he write in that email?").intent == Intent.SEARCH


def test_question_is_search():
    assert plan("when is my dentist appointment?").intent == Intent.SEARCH


def test_unknown_falls_through():
    p = plan("hmm okay then")
    assert p.intent == Intent.UNKNOWN
    assert p.confidence == 0.0


def test_merchant_parsing():
    assert parse_merchant("orders from Amazon please") == "Amazon"
    assert parse_merchant("orders i did for GYG.") == "GYG"
    assert parse_merchant("orders from nowhere lowercase") is None
