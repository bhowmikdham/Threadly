"""Gmail reads scoped to one selected thread or one explicit search page.

Mail text lives only in request/worker memory. Credentials and ownership are
resolved server-side; HTTP never runs inside the caller's DB transaction.
"""

import asyncio
import re
from datetime import UTC, datetime

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.auth.service import get_valid_access_token
from app.capabilities.service import build_capabilities
from app.db.engine import get_session_factory
from app.db.models import User
from app.sync.gmail import BASE, GmailClient, GmailError
from app.sync.worker import _message_values

ID = re.compile(r"[a-fA-F0-9]{1,32}\Z")
MAX_THREAD_MESSAGES = 200
PAGE_SIZE = 20


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ApiError(422, "gmail_id_invalid", "Use a Gmail message or thread ID.")
    return value


async def account(owner):
    async with get_session_factory()() as session:
        user = await session.get(User, owner)
        if user is None:
            raise ApiError(401, "unauthorized", "Unknown account.")
        caps = {c["id"]: c for c in build_capabilities(user)["capabilities"]}
        if not caps["gmail_read"]["ready"]:
            raise ApiError(403, "gmail_connection_required", "Connect Gmail read access.")
        version, email = user.google_account_version, user.email
    async with get_session_factory()() as session:
        token = await get_valid_access_token(session, User(id=owner))
    await check_account(owner, version)
    return token, version, email


async def check_account(owner, version):
    async with get_session_factory()() as session:
        user = await session.get(User, owner)
        if user is None or not user.google_connected or user.google_account_version != version:
            raise ApiError(
                409, "google_connection_changed", "Reconnect and select the source again."
            )


def provider_error(exc):
    code = {
        401: (401, "gmail_reauth_required"),
        403: (403, "gmail_access_denied"),
        404: (404, "gmail_source_missing"),
    }.get(exc.status, (503, "gmail_unavailable"))
    return ApiError(*code, "Gmail read failed; no saved mail fallback was used.")


def normalize(raw, thread_id, email):
    if raw.get("id") != thread_id:
        raise GmailError("Invalid thread identity")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_THREAD_MESSAGES:
        raise ApiError(422, "gmail_thread_limit", "Select a thread with at most 200 messages.")
    normalized, seen = [], set()
    for item in messages:
        mid = identifier(item.get("id"))
        if mid in seen or item.get("threadId") != thread_id:
            raise GmailError("Invalid message identity")
        seen.add(mid)
        if set(item.get("labelIds", [])) & {"SPAM", "TRASH"}:
            continue
        normalized.append(_message_values(item, email))
    normalized.sort(
        key=lambda m: (
            m["received_at"] or m["sent_at"] or datetime.min.replace(tzinfo=UTC),
            m["gmail_msg_id"],
        )
    )
    if not normalized:
        raise ApiError(404, "gmail_source_missing", "No readable messages in this thread.")
    # JSON-compatible, stable fingerprint covers bodies, reply headers and labels.
    for message in normalized:
        for field in ("received_at", "sent_at"):
            message[field] = message[field].isoformat() if message[field] else None
    return normalized


async def thread(owner, thread_id, *, transport=None):
    identifier(thread_id)
    token, version, email = await account(owner)
    client = GmailClient(token, transport=transport)
    try:
        async with asyncio.timeout(40):
            async with client._client() as http:
                raw = await client._get(http, f"{BASE}/threads/{thread_id}", {"format": "full"})
            messages = normalize(raw, thread_id, email)
    except GmailError as exc:
        raise provider_error(exc) from None
    except TimeoutError:
        raise ApiError(503, "gmail_unavailable", "Gmail read timed out.") from None
    await check_account(owner, version)
    return {
        "owner": owner,
        "account_version": version,
        "thread_id": thread_id,
        "fingerprint": digest(messages),
        "messages": messages,
    }


async def search_page(owner, query, *, page_token=None, transport=None, max_results=PAGE_SIZE):
    """One messages.list page, then bounded details. Never follows nextPageToken."""
    if type(max_results) is not int or not 1 <= max_results <= PAGE_SIZE:
        raise ValueError("Invalid page size")
    token, version, email = await account(owner)
    client = GmailClient(token, transport=transport)
    params = {"maxResults": max_results, "q": query, "includeSpamTrash": "false"}
    if page_token:
        params["pageToken"] = page_token
    try:
        async with asyncio.timeout(40):
            async with client._client() as http:
                raw = await client._get(http, f"{BASE}/messages", params)
            refs = raw.get("messages", [])
            if not isinstance(refs, list) or len(refs) > max_results:
                raise GmailError("Invalid page")
            ids = [identifier(m.get("id")) for m in refs]
            if len(set(ids)) != len(ids):
                raise GmailError("Duplicate message")
            rows = await client.get_messages_full(ids, ignore_missing=True)
            messages = []
            for row in rows:
                if set(row.get("labelIds", [])) & {"SPAM", "TRASH"}:
                    continue
                identifier(row.get("threadId"))
                values = _message_values(row, email)
                for field in ("received_at", "sent_at"):
                    values[field] = values[field].isoformat() if values[field] else None
                messages.append(values)
            next_page = raw.get("nextPageToken")
            if next_page is not None and (
                not isinstance(next_page, str) or not 0 < len(next_page) <= 1500
            ):
                raise GmailError("Invalid page token")
    except GmailError as exc:
        raise provider_error(exc) from None
    except TimeoutError:
        raise ApiError(503, "gmail_unavailable", "Gmail read timed out.") from None
    await check_account(owner, version)
    return {"messages": messages, "next_page_token": next_page, "account_version": version}
