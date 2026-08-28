import datetime as dt

from gmail_extractor import needs_tier2, parse_tier2_response, tier2_prompt

NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
MSG = {
    "gmail_msg_id": "m9",
    "from_addr": "noreply@jetstar.com",
    "sent_at": NOW,
    "subject": "Your Jetstar booking is confirmed",
    "body_text": "Your reservation is all set. Quote JQ7X9P at check-in.",
}


def test_residue_detection():
    assert needs_tier2(MSG, tier1_entities=[]) is True
    assert needs_tier2(MSG, tier1_entities=[{"type": "order"}]) is False  # tier 1 already got it
    chatty = {**MSG, "subject": "lunch?", "body_text": "burgers on friday?"}
    assert needs_tier2(chatty, tier1_entities=[]) is False  # not transactional


def test_parse_valid_response():
    raw = 'Sure! Here you go: [{"type": "order", "key": "JQ7X9P"}, {"type": "amount", "key": "$120.00"}]'
    entities = parse_tier2_response(raw, MSG)
    assert [(e["type"], e["key"]) for e in entities] == [("order", "JQ7X9P"), ("amount", "$120.00")]
    assert all(e["value"]["tier"] == 2 for e in entities)
    assert entities[0]["merchant"] == "JETSTAR"


def test_parse_rejects_junk():
    assert parse_tier2_response("no json here", MSG) == []
    assert parse_tier2_response('{"label": "x"}', MSG) == []  # object, not array
    # hallucination guards: bad type, digitless order key, empty key
    raw = '[{"type": "password", "key": "hunter2"}, {"type": "order", "key": "CONFIRMED"}, {"type": "order", "key": ""}]'
    assert parse_tier2_response(raw, MSG) == []


def test_prompt_contains_the_email_and_caps_length():
    long_msg = {**MSG, "body_text": "x" * 5000}
    prompt = tier2_prompt(long_msg)
    assert "Jetstar" in prompt
    assert len(prompt) < 2000
