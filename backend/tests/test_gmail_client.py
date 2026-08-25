"""Gmail REST client: pagination MUST walk every page; parsing MUST survive MIME trees."""
import base64

import httpx
import pytest

from app.sync.gmail import GmailClient, extract_body_text


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def gmail_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path.endswith("/messages") and "pageToken" not in params:
            return httpx.Response(200, json={
                "messages": [{"id": "m1", "threadId": "t1"}, {"id": "m2", "threadId": "t1"}],
                "nextPageToken": "page2",
            })
        if path.endswith("/messages") and params.get("pageToken") == "page2":
            return httpx.Response(200, json={"messages": [{"id": "m3", "threadId": "t2"}]})
        if "/messages/" in path:
            mid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={
                "id": mid, "threadId": "t1", "internalDate": "1755600000000",
                "payload": {
                    "mimeType": "multipart/alternative",
                    "headers": [
                        {"name": "From", "value": "Priya <p@x.com>"},
                        {"name": "Subject", "value": f"Subject {mid}"},
                    ],
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64(f"body of {mid}")}},
                    ],
                },
            })
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "me@x.com", "historyId": "9000"})
        if path.endswith("/history"):
            return httpx.Response(200, json={
                "historyId": "9001",
                "history": [{"messagesAdded": [{"message": {"id": "m9", "threadId": "t9"}}]}],
            })
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_list_walks_all_pages():
    client = GmailClient("tok", transport=gmail_transport())
    refs = await client.list_all_message_ids()
    assert [r["id"] for r in refs] == ["m1", "m2", "m3"]  # both pages, in order


@pytest.mark.asyncio
async def test_get_messages_preserves_order():
    client = GmailClient("tok", transport=gmail_transport())
    details = await client.get_messages_full(["m2", "m1"])
    assert [d["id"] for d in details] == ["m2", "m1"]


@pytest.mark.asyncio
async def test_history_since_collects_added_ids():
    client = GmailClient("tok", transport=gmail_transport())
    changed, newest = await client.history_since("8999")
    assert changed == ["m9"] and newest == "9001"


def test_extract_prefers_plain_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>html <b>ver</b></p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("plain ver")}},
        ],
    }
    assert extract_body_text(payload) == "plain ver"


def test_extract_falls_back_to_stripped_html():
    payload = {"mimeType": "text/html", "body": {"data": _b64("<div>hello<br>world</div>")}}
    assert extract_body_text(payload) == "hello\nworld"
