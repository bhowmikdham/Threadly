"""Bounded, read-only Gmail evidence lookup. Absence never proves non-execution."""

import asyncio
import base64
import json
import re
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

import httpx

from app.actions.gmail_sender import ID, Outcome
from app.model_client.structured import reject_duplicate_keys

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_PAGES = 3
MAX_CANDIDATES = 20
MAX_BYTES = 192000


class EvidenceUnavailable(Exception):
    """Sanitized reason only; never retain provider response or mail text."""


def unknown(code):
    return Outcome("outcome_unknown", code)


async def get(client, path, params=None):
    async with client.stream("GET", BASE + path, params=params) as response:
        if response.status_code != 200:
            raise EvidenceUnavailable(
                "read_access_unavailable"
                if response.status_code in {401, 403}
                else "read_unavailable"
            )
        data = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=4096):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise EvidenceUnavailable("read_response_too_large")
        try:
            body = json.loads(data, object_pairs_hook=reject_duplicate_keys)
        except (ValueError, UnicodeError):
            raise EvidenceUnavailable("invalid_read_response") from None
        if not isinstance(body, dict):
            raise EvidenceUnavailable("invalid_read_response")
        return body


def decode(raw):
    if not isinstance(raw, str) or len(raw) > 172000:
        raise ValueError
    return base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)


def semantic(raw):
    """Plain text only; normalize encoding/folding/line endings, never body whitespace."""
    message = BytesParser(policy=policy.default).parsebytes(decode(raw))
    fields = (
        "from",
        "to",
        "cc",
        "bcc",
        "subject",
        "message-id",
        "date",
        "in-reply-to",
        "references",
        "content-type",
        "content-transfer-encoding",
        "mime-version",
        "reply-to",
        "sender",
    )
    if message.defects or message.is_multipart() or message.get_content_type() != "text/plain":
        raise ValueError
    for name in fields:
        headers = message.get_all(name, [])
        if len(headers) > 1 or any(getattr(h, "defects", ()) for h in headers):
            raise ValueError
    if message.get_content_disposition() is not None or message.get_filename() is not None:
        raise ValueError
    result = {}
    for name in ("from", "to", "cc", "bcc", "sender", "reply-to"):
        header = message[name]
        # Display names/order do not alter envelope addresses. Duplicates do.
        addresses = sorted(a.addr_spec for a in header.addresses) if header else []
        if any(not a or "@" not in a for a in addresses) or len(set(addresses)) != len(addresses):
            raise ValueError
        result[name] = addresses
    for name in ("subject", "message-id", "in-reply-to"):
        result[name] = str(message[name]) if message[name] is not None else None
    result["references"] = str(message.get("references", "")).split()
    result["date"] = parsedate_to_datetime(str(message["date"]))
    if result["date"].tzinfo is None:
        raise ValueError
    result["body"] = message.get_content(errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    if message.defects:  # Transfer decoding can add defects.
        raise ValueError
    return result


def matches(message, expected, thread_id, start, end, candidate_id):
    if (
        message.get("id") != candidate_id
        or not isinstance(message.get("threadId"), str)
        or not ID.fullmatch(message["threadId"])
        or (thread_id and message["threadId"] != thread_id)
        or not isinstance(message.get("labelIds"), list)
        or "SENT" not in message["labelIds"]
    ):
        return False
    try:
        stamp = message["internalDate"]
        if not isinstance(stamp, str) or not stamp.isdigit():
            return False
        if not start <= int(stamp) / 1000 <= end:
            return False
        return semantic(message.get("raw")) == expected
    except (ValueError, TypeError, KeyError, LookupError, AttributeError, OverflowError):
        return False


async def lookup(token, payload, dispatched_at, *, late_response=None, transport=None):
    try:
        expected = semantic(payload["mime_base64url"])
        mid = expected["message-id"]
        # Only backend-generated UUID Message-IDs may become search syntax.
        if not isinstance(mid, str) or not re.fullmatch(r"<[a-f0-9-]{36}@[A-Za-z0-9.-]+>", mid):
            return unknown("invalid_recovery_payload")
        start = int((dispatched_at - timedelta(minutes=5)).timestamp())
        end = int((dispatched_at + timedelta(hours=1)).timestamp())
        async with asyncio.timeout(35):
            async with httpx.AsyncClient(
                transport=transport,
                timeout=10,
                follow_redirects=False,
                trust_env=False,
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                profile = await get(client, "/profile")
                if profile.get("emailAddress") != payload["preview"]["from_address"]:
                    return unknown("provider_account_mismatch")
                params = {
                    "q": f"rfc822msgid:{mid} after:{start} before:{end}",
                    "labelIds": "SENT",
                    "maxResults": MAX_CANDIDATES,
                }
                seen_pages, candidates = set(), {}
                for _ in range(MAX_PAGES):
                    page = await get(client, "/messages", params)
                    values = page.get("messages", [])
                    if not isinstance(values, list) or len(values) > MAX_CANDIDATES:
                        return unknown("incomplete_search")
                    for value in values:
                        if (
                            not isinstance(value, dict)
                            or not isinstance(value.get("id"), str)
                            or not ID.fullmatch(value["id"])
                            or not isinstance(value.get("threadId"), str)
                            or not ID.fullmatch(value["threadId"])
                            or value["id"] in candidates
                        ):
                            return unknown("invalid_search_candidates")
                        candidates[value["id"]] = value["threadId"]
                    if len(candidates) > 1:
                        return unknown("multiple_candidates")
                    page_token = page.get("nextPageToken")
                    if page_token is None:
                        break
                    if (
                        not isinstance(page_token, str)
                        or not page_token
                        or len(page_token) > 2048
                        or page_token in seen_pages
                    ):
                        return unknown("incomplete_search")
                    seen_pages.add(page_token)
                    params["pageToken"] = page_token
                else:
                    return unknown("incomplete_search")
                if not candidates:
                    return unknown("not_observed")
                candidate_id, listed_thread = next(iter(candidates.items()))
                detail = await get(client, f"/messages/{candidate_id}", {"format": "raw"})
                if listed_thread != detail.get("threadId") or not matches(
                    detail,
                    expected,
                    payload["preview"]["gmail_thread_id"],
                    start,
                    end,
                    candidate_id,
                ):
                    return unknown("candidate_mismatch")
                # Missing/normalized-away Bcc is a mismatch, never inferred from To/Cc.
                if late_response and (
                    late_response.get("state") == "failed"
                    or (
                        late_response.get("state") == "succeeded"
                        and (
                            late_response.get("message_id") != candidate_id
                            or late_response.get("thread_id") != detail["threadId"]
                        )
                    )
                ):
                    return unknown("conflicting_late_response")
                return Outcome(
                    "succeeded", "sent_evidence_matched", candidate_id, detail["threadId"]
                )
    except EvidenceUnavailable as error:
        return unknown(str(error))
    except (httpx.HTTPError, TimeoutError):
        return unknown("read_unavailable")
    except (ValueError, TypeError, KeyError, LookupError, AttributeError, OverflowError):
        return unknown("invalid_recovery_payload")
