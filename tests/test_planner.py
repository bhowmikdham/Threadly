from orch_planner import parse_key, parse_merchant, parse_window_days, plan
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
    # E7: bare units count as one
    assert parse_window_days("last month") == 30
    assert parse_window_days("past week") == 7
    assert parse_window_days("last year") == 365


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


def test_commitments_lookup():
    assert plan("what did I promise people this week?").params.get("type") == "commitment"
    assert plan("show me my deadlines").intent == Intent.FETCH_ENTITY
    assert plan("what's due soon?").params.get("type") == "commitment"


def test_question_is_search():
    assert plan("when is my dentist appointment?").intent == Intent.SEARCH


def test_unknown_falls_through():
    p = plan("hmm okay then")
    assert p.intent == Intent.UNKNOWN
    assert p.confidence == 0.0


def test_merchant_parsing():
    assert parse_merchant("orders from Amazon please") == "Amazon"
    assert parse_merchant("orders i did for GYG.") == "GYG"
    assert parse_merchant("all the orders i did for guzman y gomez") == "guzman y gomez"
    # time phrases after the trigger word are not merchants
    assert parse_merchant("orders from the last 2 months") is None
    assert parse_merchant("orders from yesterday") is None


def test_e1_second_trigger_word_never_leaks_into_merchant():
    p = plan("invoices from GYG from last month")
    assert p.params["merchant"] == "GYG"
    assert p.params["window_days"] == 30


def test_e3_summarise_vs_merchant_lookup_precedence():
    assert plan("summarise my GYG orders").intent == Intent.FETCH_ENTITY
    assert plan("summarise my GYG orders", has_thread=True).intent == Intent.SUMMARISE
    assert plan("summarise this thread").intent == Intent.SUMMARISE
    assert plan("tl;dr please", has_thread=True).intent == Intent.SUMMARISE


def test_e4_pasted_key_becomes_a_filter():
    p = plan("order GYG-84640")
    assert p.intent == Intent.FETCH_ENTITY and p.params["key"] == "GYG-84640"
    assert parse_key("invoice INV-2043 please") == "INV-2043"
    assert parse_key("reference 8471023941") == "8471023941"
    assert "key" not in plan("show me all my orders from GYG").params
    assert parse_key("orders from the last 2 months") is None


def test_e5_possessive_merchant():
    assert plan("show my gyg orders").params.get("merchant") == "gyg"
    assert parse_merchant("our amazon receipts") == "amazon"
    assert parse_merchant("show my orders") is None  # no merchant word present
