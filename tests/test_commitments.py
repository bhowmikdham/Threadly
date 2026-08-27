import datetime as dt

from gmail_extractor import extract_commitments, parse_due

# a Tuesday
BASE = dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc)


def _msg(body: str, is_sent: bool = False) -> dict:
    return {"gmail_msg_id": "m1", "sent_at": BASE, "is_sent": is_sent, "body_text": body}


def test_inbound_asks():
    ask, pack = extract_commitments(_msg("Can you send me the Q3 numbers by Thursday? Board pack is due Friday."))
    assert ask["direction"] == "inbound"
    assert "Q3 numbers by Thursday" in ask["description"]
    assert ask["due_at"].weekday() == 3   # Thursday
    assert pack["due_at"].weekday() == 4  # Friday


def test_outbound_promise():
    [c] = extract_commitments(_msg("I'll have a draft over to you by Wednesday EOD.", is_sent=True))
    assert c["direction"] == "outbound"
    assert c["due_at"].weekday() == 2


def test_no_deadline_no_commitment():
    assert extract_commitments(_msg("Great meeting you at the meetup, let's grab coffee.")) == []


def test_parse_due():
    assert parse_due("thursday", BASE).date() == dt.date(2026, 8, 27)
    assert parse_due("tuesday", BASE).date() == BASE.date()  # same-day name = today
    assert parse_due("tomorrow", BASE).date() == dt.date(2026, 8, 26)
    assert parse_due("end of week", BASE).date() == dt.date(2026, 8, 28)  # Friday
    assert parse_due("someday", BASE) is None
