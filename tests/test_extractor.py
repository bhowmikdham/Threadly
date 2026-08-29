import datetime as dt

from gmail_extractor import extract, merchant_from_addr

NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)


def _msg(body: str, subject: str = "", from_addr: str = "orders@gyg.com.au") -> dict:
    return {"gmail_msg_id": "m1", "from_addr": from_addr, "sent_at": NOW, "subject": subject, "body_text": body}


def test_order_number():
    [entity] = [e for e in extract(_msg("Order number: GYG-84721\nEnjoy!")) if e["type"] == "order"]
    assert entity["key"] == "GYG-84721"
    assert entity["merchant"] == "GYG"
    assert entity["source_msg_id"] == "m1"


def test_subject_and_body_dedupe():
    entities = extract(_msg("Order number: GYG-84721", subject="Your order confirmation #GYG-84721"))
    assert len([e for e in entities if e["type"] == "order"]) == 1


def test_tracking_and_amount():
    entities = extract(_msg("Tracking number: 33AUS7712345678. Total charged: $29.00"))
    types = {e["type"]: e["key"] for e in entities}
    assert types["tracking"] == "33AUS7712345678"
    assert types["amount"] == "$29.00"


def test_no_digit_no_order():
    # "order CONFIRMED" is not an order number
    assert [e for e in extract(_msg("Your order CONFIRMED yesterday")) if e["type"] == "order"] == []


def test_merchant_from_addr():
    assert merchant_from_addr("orders@gyg.com.au") == "GYG"
    assert merchant_from_addr("noreply@auspost.com.au") == "AUSPOST"
    assert merchant_from_addr(None) is None
    assert merchant_from_addr("not-an-email") is None


def test_merchant_from_subdomain_senders():
    # real merchants send from mail-infrastructure subdomains
    assert merchant_from_addr("no-reply@em.gyg.com.au") == "GYG"
    assert merchant_from_addr("track@orders.uber.com") == "UBER"
    assert merchant_from_addr("auto-confirm@amazon.com.au") == "AMAZON"
