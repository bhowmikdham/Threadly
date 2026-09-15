"""Native owner-filtered local search with sync-fenced, expiring keyset pagination."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import DBAPIError

from app.api.errors import ApiError
from app.assistant.summary import digest
from app.config import get_settings
from app.db.models import Message, Thread, User
from app.schemas.mail_search import MailSearchRequest

VERSION = "scoped-mail-search-1.0.0"
PAGE_SIZE = 20
QUOTE_LIMIT = 1000
CURSOR_MINUTES = 15


def _encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _signature(encoded):
    return hmac.new(
        get_settings().secret_key.encode(), (VERSION + ":" + encoded).encode(), hashlib.sha256
    ).digest()


def make_cursor(owner, scope, version, position, expires_at):
    payload = {
        "owner": owner,
        "scope": scope,
        "version": version,
        "position": position,
        "expires_at": expires_at,
    }
    encoded = _encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return encoded + "." + _encode(_signature(encoded))


def parse_cursor(value, owner, scope, version, now):
    try:
        encoded, signature = value.split(".")
        if not hmac.compare_digest(_signature(encoded), _decode(signature)):
            raise ValueError("signature")
        payload = json.loads(_decode(encoded))
        if set(payload) != {"owner", "scope", "version", "position", "expires_at"}:
            raise ValueError("shape")
        if payload["owner"] != owner or payload["scope"] != scope:
            raise ValueError("scope")
        if not isinstance(payload["expires_at"], int) or payload["expires_at"] <= now.timestamp():
            raise ValueError("expiry")
        if payload["version"] != version:
            raise ApiError(409, "mail_search_changed", "Mailbox sync changed; restart this search.")
        position = payload["position"]
        if not isinstance(position, dict) or set(position) != {"time", "id"}:
            raise ValueError("position")
        # Reuse strict offset-aware date parsing without accepting naive timestamps.
        from pydantic import AwareDatetime, TypeAdapter

        timestamp = TypeAdapter(AwareDatetime).validate_json(json.dumps(position["time"]))
        if type(position["id"]) is not int or position["id"] < 1:
            raise ValueError("id")
        return timestamp, position["id"], payload["expires_at"]
    except ApiError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ApiError(
            409, "mail_search_cursor_invalid", "Restart search with its original scope."
        ) from None


async def search(session, owner: int, request: MailSearchRequest):
    try:
        # Bound both lock wait and query execution. These settings end with this transaction.
        await session.execute(select(func.set_config("lock_timeout", "1000ms", True)))
        await session.execute(select(func.set_config("statement_timeout", "3000ms", True)))
        # Sync acquires this same user row exclusively before changing mail/version.
        user = await session.scalar(
            select(User)
            .where(User.id == owner)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if user is None:
            raise ApiError(401, "unauthorized", "Unknown account.")
        now = await session.scalar(select(func.clock_timestamp()))
        scope = digest({"policy": VERSION, **request.model_dump(mode="json", exclude={"cursor"})})
        after = None
        expires_at = int((now + timedelta(minutes=CURSOR_MINUTES)).timestamp())
        if request.cursor:
            timestamp, message_id, expires_at = parse_cursor(
                request.cursor, owner, scope, user.sync_version, now
            )
            after = (timestamp, message_id)
        received = func.coalesce(Message.received_at, Message.sent_at)
        mid = Message.id
        body = func.coalesce(Message.body_clean, "")
        # Parameterized strpos: %, _, quotes and Gmail operators stay literal text.
        position = func.strpos(func.lower(body), func.lower(request.query))
        start = func.greatest(1, position - 150)
        query = (
            select(
                Message.id.label("cursor_id"),
                Message.gmail_msg_id.label("message_id"),
                Thread.gmail_thread_id.label("thread_id"),
                Thread.version.label("thread_version"),
                Message.updated_at.label("message_updated_at"),
                received.label("received"),
                Message.received_at.label("provider_received"),
                func.substr(body, start, QUOTE_LIMIT).label("quote"),
                start.label("quote_start"),
                func.length(body).label("body_length"),
            )
            .join(Thread, Thread.id == Message.thread_id)
            .where(
                Message.user_id == owner,
                Thread.user_id == owner,
                received >= request.received_from,
                received < request.received_before,
                position > 0,
            )
        )
        if request.folder != "all_synced":
            query = query.where(Message.reply_metadata["label_ids"].contains([request.folder]))
        if after:
            query = query.where(
                or_(received < after[0], and_(received == after[0], mid < after[1]))
            )
        rows = (
            await session.execute(query.order_by(received.desc(), mid.desc()).limit(PAGE_SIZE + 1))
        ).all()
        page = rows[:PAGE_SIZE]
        results = []
        for row in page:
            source_version = digest(
                {
                    "message_id": row.message_id,
                    "thread_version": row.thread_version,
                    "message_updated_at": row.message_updated_at.isoformat(),
                    "policy": VERSION,
                }
            )
            results.append(
                {
                    "message_id": row.message_id,
                    "thread_id": row.thread_id,
                    "thread_version": row.thread_version,
                    "source_version": source_version,
                    "received_at": row.received.isoformat(),
                    "date_basis": "provider_received_at"
                    if row.provider_received
                    else "legacy_sent_at",
                    "quote": row.quote,
                    "quote_start": row.quote_start - 1,
                    "quote_truncated": row.body_length > len(row.quote),
                    "evidence_ref": "mail-"
                    + digest({"owner": owner, "id": row.message_id, "version": source_version})[
                        :32
                    ],
                }
            )
        cursor = None
        if len(rows) > PAGE_SIZE:
            last = page[-1]
            cursor = make_cursor(
                owner,
                scope,
                user.sync_version,
                {"time": last.received.isoformat(), "id": last.cursor_id},
                expires_at,
            )
        return {
            "schema_version": "1.0",
            "policy_version": VERSION,
            "scope": request.model_dump(mode="json", exclude={"cursor"}),
            "results": results,
            "next_cursor": cursor,
            "coverage": {
                "source": "locally_synced_cleaned_bodies",
                "complete": False,
                "live_verified": False,
                "sync_version": user.sync_version,
                "last_synced_at": user.last_synced_at.isoformat() if user.last_synced_at else None,
                "sync_status": "synced_snapshot" if user.last_synced_at else "not_yet_verified",
                "limits": [
                    "No live Gmail query or attachment search.",
                    "Messages without usable dates or bodies cannot match.",
                    "Folder filters exclude rows without known matching labels.",
                    "Sync normally excludes Spam and Trash; all_synced means stored rows only.",
                ],
            },
            "empty_result_meaning": (
                "No matching local text within this scope; Gmail completeness is unknown."
            )
            if not results
            else None,
        }
    except DBAPIError as exc:
        code = getattr(exc.orig, "sqlstate", None)
        if code in {"57014", "55P03"}:
            raise ApiError(
                503,
                "mail_search_busy",
                "Search could not finish within its budget; retry or narrow the scope.",
            ) from None
        raise
