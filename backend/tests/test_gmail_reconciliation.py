"""Provider normalization and conservative bounded Sent evidence fixtures."""

import base64
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from app.actions import email_payload
from app.actions import gmail_reconciliation as gmail

NOW = datetime(2026, 9, 16, 2, tzinfo=UTC)


def payload(*, bcc=False, reply=False):
    return email_payload.build(
        {
            "from_address": "owner@example.test",
            "to": ["to@example.test"],
            "cc": ["cc@example.test"],
            "bcc": ["hidden@example.test"] if bcc else [],
        },
        {
            "subject": "Café discussion",
            "body": "First line.\nSecond line.\n",
            "unresolved_fields": [],
        },
        sender="owner@example.test",
        now=NOW,
        identifier="00000000-0000-0000-0000-000000000001",
        reply={
            "in_reply_to": "<parent@example.test>",
            "references": ["<parent@example.test>"],
            "gmail_thread_id": "thread-one",
        }
        if reply
        else None,
    )


def changed_raw(value, mutation):
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(value["mime_base64url"])
    )
    mutation(message)
    return base64.urlsafe_b64encode(message.as_bytes(policy=policy.SMTP)).decode()


class Sent:
    def __init__(self, value, at=NOW, *, pages=None, detail=None, profile=None):
        self.value, self.at, self.calls = value, at, []
        self.profile = (
            {"emailAddress": value["preview"]["from_address"]} if profile is None else profile
        )
        self.pages = (
            pages
            if pages is not None
            else [{"messages": [{"id": "sent-one", "threadId": "thread-one"}]}]
        )
        self.detail = {
            "id": "sent-one",
            "threadId": "thread-one",
            "labelIds": ["SENT"],
            "internalDate": str(int(at.timestamp() * 1000)),
            "raw": value["mime_base64url"],
            **(detail or {}),
        }

    def __call__(self, request):
        self.calls.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/profile"):
            return httpx.Response(200, json=self.profile)
        if request.url.path.endswith("/messages"):
            assert request.url.params["labelIds"] == "SENT"
            query = request.url.params["q"]
            assert "rfc822msgid:" + self.value["preview"]["message_id"] in query
            assert "after:" in query and "before:" in query
            index = int(request.url.params.get("pageToken", "0"))
            return httpx.Response(200, json=self.pages[index])
        assert request.url.path.endswith("/messages/sent-one")
        assert request.url.params["format"] == "raw"
        return httpx.Response(200, json=self.detail)


@pytest.mark.parametrize("bcc,reply", [(False, False), (True, False), (False, True)])
async def test_complete_semantic_match_and_page_coverage(bcc, reply):
    value = payload(bcc=bcc, reply=reply)
    sent = Sent(
        value,
        pages=[
            {"nextPageToken": "1"},
            {"messages": [{"id": "sent-one", "threadId": "thread-one"}]},
        ],
    )
    result = await gmail.lookup("SECRET", value, NOW, transport=httpx.MockTransport(sent))
    assert result.state == "succeeded" and result.message_id == "sent-one"
    assert len(sent.calls) == 4


async def test_transfer_encoding_header_folding_display_names_and_crlf_normalize():
    value = payload()

    def normalized(message):
        body = message.get_content()
        message.set_content(body, charset="utf-8", cte="quoted-printable")
        message.replace_header("To", "Display Name <to@example.test>")
        message["X-Google-Test"] = "ignored-provider-header"

    sent = Sent(value, detail={"raw": changed_raw(value, normalized)})
    assert (
        await gmail.lookup("token", value, NOW, transport=httpx.MockTransport(sent))
    ).state == "succeeded"


@pytest.mark.parametrize(
    "change",
    [
        "subject",
        "body",
        "to",
        "cc",
        "from",
        "message-id",
        "date",
        "bcc",
        "reply",
        "references",
        "duplicate",
        "attachment",
        "html",
        "charset",
        "raw",
        "thread",
        "label",
        "timestamp",
        "id",
    ],
)
async def test_mismatch_never_proves_send(change):
    value = payload(bcc=True, reply=True)

    def mutate(message):
        if change in {"subject", "to", "cc", "from", "message-id", "date"}:
            message.replace_header(
                change,
                {
                    "subject": "Other",
                    "to": "wrong@example.test",
                    "cc": "wrong@example.test",
                    "from": "wrong@example.test",
                    "message-id": "<other@example.test>",
                    "date": "Tue, 15 Sep 2026 02:00:00 +0000",
                }[change],
            )
        elif change == "body":
            message.set_content("First line.\nSecond line. \n")
        elif change == "bcc":
            del message["Bcc"]
        elif change == "reply":
            message.replace_header("In-Reply-To", "<other@example.test>")
        elif change == "references":
            del message["References"]
        elif change == "duplicate":
            message._headers.append(("Subject", "Another subject"))
        elif change == "attachment":
            message.add_attachment(b"data", maintype="application", subtype="octet-stream")
        elif change == "html":
            message.set_content("<p>body</p>", subtype="html")
        elif change == "charset":
            message.set_param("charset", "unknown-charset")

    detail = {"raw": changed_raw(value, mutate)}
    detail.update(
        {
            "raw": {"raw": "%%%"},
            "thread": {"threadId": "other"},
            "label": {"labelIds": ["INBOX"]},
            "timestamp": {"internalDate": "1"},
            "id": {"id": "different"},
        }.get(change, {})
    )
    result = await gmail.lookup(
        "token", value, NOW, transport=httpx.MockTransport(Sent(value, detail=detail))
    )
    assert result.state == "outcome_unknown"


@pytest.mark.parametrize(
    "pages,code",
    [
        ([{}], "not_observed"),
        (
            [
                {"messages": [{"id": "sent-one", "threadId": "thread-one"}], "nextPageToken": "1"},
                {"messages": [{"id": "other", "threadId": "thread-one"}]},
            ],
            "multiple_candidates",
        ),
        (
            [
                {"messages": [{"id": "sent-one", "threadId": "thread-one"}], "nextPageToken": "1"},
                {"messages": [{"id": "sent-one", "threadId": "thread-one"}]},
            ],
            "invalid_search_candidates",
        ),
        ([{"nextPageToken": "1"}, {"nextPageToken": "1"}], "incomplete_search"),
        (
            [{"nextPageToken": "1"}, {"nextPageToken": "2"}, {"nextPageToken": "3"}],
            "incomplete_search",
        ),
        ([{"messages": "PRIVATE"}], "incomplete_search"),
        ([{"messages": [{"id": "../../other", "threadId": "t"}]}], "invalid_search_candidates"),
    ],
)
async def test_search_absence_duplicates_and_bounds(pages, code):
    value = payload()
    sent = Sent(value, pages=pages)
    result = await gmail.lookup("token", value, NOW, transport=httpx.MockTransport(sent))
    assert result.state == "outcome_unknown" and result.code == code
    assert len(sent.calls) <= 4


@pytest.mark.parametrize(
    "mode",
    ["401", "403", "429", "500", "redirect", "timeout", "oversize", "malformed", "duplicate"],
)
async def test_provider_errors_sanitized_and_no_retry(mode, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        if mode.isdigit():
            return httpx.Response(int(mode), text="PRIVATE")
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://private.invalid"})
        if mode == "timeout":
            raise httpx.ReadTimeout("PRIVATE secret")
        return httpx.Response(
            200,
            content={
                "oversize": b"PRIVATE" * 30000,
                "malformed": b"PRIVATE",
                "duplicate": b'{"emailAddress":"a","emailAddress":"b"}',
            }[mode],
        )

    result = await gmail.lookup("PRIVATE", payload(), NOW, transport=httpx.MockTransport(handler))
    assert result.state == "outcome_unknown" and len(calls) == 1
    assert "PRIVATE" not in str(result.evidence()) + caplog.text


async def test_wrong_provider_account_stops_before_mail_read():
    sent = Sent(payload(), profile={"emailAddress": "other@example.test"})
    result = await gmail.lookup("token", sent.value, NOW, transport=httpx.MockTransport(sent))
    assert result.code == "provider_account_mismatch" and len(sent.calls) == 1


@pytest.mark.parametrize(
    "late",
    [
        {"state": "failed"},
        {"state": "succeeded", "message_id": "other", "thread_id": "thread-one"},
    ],
)
async def test_conflicting_late_observation_stays_unknown(late):
    sent = Sent(payload())
    result = await gmail.lookup(
        "token", sent.value, NOW, late_response=late, transport=httpx.MockTransport(sent)
    )
    assert result.code == "conflicting_late_response"
