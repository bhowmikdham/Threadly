"""Golden-style tests for the deterministic cleaner (module 3)."""
from app.sync.cleaning import clean_body


def test_strips_quoted_reply_block():
    raw = (
        "Sounds good, see you then.\n\n"
        "On Mon, 3 Aug 2026 at 14:22, Priya K <p@x.com> wrote:\n> earlier text\n> more quote"
    )
    assert clean_body(raw) == "Sounds good, see you then."


def test_strips_signature_marker():
    raw = "Attached the report.\n-- \nBhowmik Dham\nMonash University"
    assert clean_body(raw) == "Attached the report."


def test_strips_sent_from_my():
    raw = "Running late, start without me\n\nSent from my iPhone"
    assert clean_body(raw) == "Running late, start without me"


def test_drops_angle_quote_lines_and_squeezes():
    raw = "Reply here\n> old line one\n> old line two\n\n\n\nend"
    assert clean_body(raw) == "Reply here\n\nend"


def test_empty_and_none_safe():
    assert clean_body("") == ""
