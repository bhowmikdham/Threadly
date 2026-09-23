"""Explicit Gmail search with signed, account/scope-bound one-page cursors."""

import base64
import hashlib
import hmac
import json
import time

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.config import get_settings
from app.mail import live

VERSION = "gmail-on-demand-search-1.0"


def sign(raw):
    return hmac.new(
        get_settings().secret_key.encode(), (VERSION + raw).encode(), hashlib.sha256
    ).hexdigest()


def encode_cursor(owner, version, scope, token):
    raw = base64.urlsafe_b64encode(
        json.dumps(
            {
                "owner": owner,
                "version": version,
                "scope": scope,
                "token": token,
                "expires": int(time.time()) + 900,
            },
            separators=(",", ":"),
        ).encode()
    ).decode()
    return raw + "." + sign(raw)


def decode_cursor(value, owner, version, scope):
    try:
        raw, signature = value.split(".")
        if not hmac.compare_digest(sign(raw), signature):
            raise ValueError()
        data = json.loads(base64.b64decode(raw, altchars=b"-_", validate=True))
        if (
            data["owner"] != owner
            or data["version"] != version
            or data["scope"] != scope
            or data["expires"] <= time.time()
        ):
            raise ValueError()
        if not isinstance(data["token"], str) or not 0 < len(data["token"]) <= 1500:
            raise ValueError()
        return data["token"]
    except (ValueError, TypeError, KeyError):
        raise ApiError(
            409, "mail_search_cursor_invalid", "Restart search with its original scope."
        ) from None


async def page(owner, query, scope, cursor=None):
    _, version, _ = await live.account(owner)
    token = decode_cursor(cursor, owner, version, scope) if cursor else None
    result = await live.search_page(owner, query, page_token=token)
    if result["account_version"] != version:
        raise ApiError(409, "google_connection_changed", "Restart search.")
    next_cursor = (
        encode_cursor(owner, version, scope, result["next_page_token"])
        if result["next_page_token"]
        else None
    )
    return result["messages"], next_cursor


async def search(owner, request):
    # Query text cannot inject Gmail operators that widen the explicit folder/date scope.
    if any(c in {'"', "\\"} or ord(c) < 32 or ord(c) == 127 for c in request.query):
        raise ApiError(
            422, "mail_search_query_invalid", "Use search words without quotes or escapes."
        )
    query = (
        f'"{request.query}" -in:spam -in:trash '
        f"after:{int(request.received_from.timestamp())} "
        f"before:{int(request.received_before.timestamp())}"
    )
    if request.folder in {"INBOX", "SENT"}:
        query += " in:" + request.folder.lower()
    scope = digest({"policy": VERSION, **request.model_dump(mode="json", exclude={"cursor"})})
    messages, cursor = await page(owner, query, scope, request.cursor)
    results = []
    for m in messages:
        # Enforce scope again locally; Google's search may include headers/attachments.
        from datetime import datetime

        received = datetime.fromisoformat(m["received_at"]) if m["received_at"] else None
        if received is None or not request.received_from <= received < request.received_before:
            continue
        if (
            request.folder in {"INBOX", "SENT"}
            and request.folder not in m["reply_metadata"]["label_ids"]
        ):
            continue
        results.append(
            {
                "message_id": m["gmail_msg_id"],
                "thread_id": m["gmail_thread_id"],
                "subject": m["subject"],
                "received_at": m["received_at"],
                "quote": m["body_clean"][:1000],
                "quote_truncated": len(m["body_clean"]) > 1000,
                "source_version": digest(m),
            }
        )
    return {
        "schema_version": "2.0",
        "policy_version": VERSION,
        "scope": request.model_dump(mode="json", exclude={"cursor"}),
        "results": results,
        "next_cursor": cursor,
        "coverage": {
            "source": "live_gmail_query",
            "complete": False,
            "live_verified": True,
            "persisted_email_content": False,
            "limits": [
                "One page, at most 20 message details; request next_cursor explicitly.",
                "Gmail search includes headers; an excerpt is not proof of a body match.",
                "No attachment download; mailbox may change between pages.",
            ],
        },
        "empty_result_meaning": "No returned matches in this page and scope."
        if not results
        else None,
    }
