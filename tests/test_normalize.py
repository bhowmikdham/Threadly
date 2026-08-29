import base64

from gmail_normalize import clean_email_text, extract_body, html_to_text


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


HTML = (
    "<html><head><style>.a{color:red}</style><script>var x=1;</script></head>"
    "<body><div>Thanks for your order!</div>"
    "<table><tr><td>Order number:</td><td>GYG-84990</td></tr></table>"
    "<p>Total charged: $21.00</p></body></html>"
)


def test_html_to_text_keeps_content_drops_markup():
    text = html_to_text(HTML)
    assert "Order number: GYG-84990" in text  # table cells joined, regex-extractable
    assert "Total charged: $21.00" in text
    assert "var x" not in text and "color:red" not in text and "<" not in text


def test_extract_body_prefers_plain_falls_back_to_html():
    both = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": b64("plain body")}},
        {"mimeType": "text/html", "body": {"data": b64("<p>html body</p>")}},
    ]}
    assert extract_body(both) == "plain body"

    html_only = {"mimeType": "text/html", "body": {"data": b64("<p>Order number: X-123</p>")}}
    assert "Order number: X-123" in extract_body(html_only)

    assert extract_body({"mimeType": "text/plain", "body": {}}) == ""


def test_clean_strips_forward_banner_and_signature():
    raw = (
        "=== Forwarded by Priya Sharma on 01/01/2019 at 3:00pm ===\n"
        "Can you send the Q3 numbers?\n"
        "-- \n"
        "Priya Sharma | Acme Corp\n0400 123 456"
    )
    assert clean_email_text(raw) == "Can you send the Q3 numbers?"


def test_clean_strips_original_message_marker():
    raw = "Sounds good.\n-----Original Message-----\nolder quoted text"
    assert clean_email_text(raw) == "Sounds good.\n\nolder quoted text"


def test_clean_leaves_normal_mail_alone():
    raw = "Hey — can you send me the Q3 numbers by Thursday?\n\nCheers,\nPriya"
    assert clean_email_text(raw) == raw
