"""Bound OAuth ingress before validation and count attempts across API workers."""

import hashlib
import hmac
import math
from datetime import timedelta

from fastapi import Request
from fastapi.routing import APIRoute
from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import ApiError
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import OAuthRateLimit

_MAX_BODY_BYTES = 8192
_WINDOW = timedelta(minutes=1)
_RETENTION = timedelta(minutes=2)
_RATE_PATHS = {
    "/auth/google/begin": "begin",
    "/auth/google/exchange": "exchange",
}


async def _bounded_body(request: Request) -> None:
    """Cache only a small body so FastAPI can validate it normally afterwards."""
    size = request.headers.get("content-length")
    if size is not None:
        try:
            if int(size) > _MAX_BODY_BYTES:
                raise ApiError(
                    413,
                    "request_too_large",
                    "Google sign-in request is too large.",
                    headers={"Cache-Control": "no-store"},
                )
        except ValueError:
            pass  # The streaming limit remains authoritative.
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            raise ApiError(
                413,
                "request_too_large",
                "Google sign-in request is too large.",
                headers={"Cache-Control": "no-store"},
            )
        chunks.append(chunk)
    request._body = b"".join(chunks)


def _bucket_key(action: str, peer: str) -> str:
    # A keyed digest keeps raw addresses out of persistent state and query logs.
    data = f"oauth-rate-v1\0{action}\0{peer}".encode()
    return hmac.new(get_settings().secret_key.encode(), data, hashlib.sha256).hexdigest()


async def _increment(session, key: str, *, maximum: int, now):
    reset = OAuthRateLimit.window_start <= now - _WINDOW
    command = (
        insert(OAuthRateLimit)
        .values(
            bucket_key=key,
            window_start=now,
            request_count=1,
            expires_at=now + _RETENTION,
        )
        .on_conflict_do_update(
            index_elements=[OAuthRateLimit.bucket_key],
            set_={
                "window_start": case((reset, now), else_=OAuthRateLimit.window_start),
                "request_count": case(
                    (reset, 1),
                    else_=func.least(OAuthRateLimit.request_count + 1, maximum + 1),
                ),
                "expires_at": now + _RETENTION,
            },
        )
        .returning(OAuthRateLimit.request_count, OAuthRateLimit.window_start)
    )
    return (await session.execute(command)).one()


async def enforce(request: Request, action: str) -> None:
    """Persist attempts before OAuth state consumption or Google network calls.

    Only the ASGI peer address is trusted. Ingress must present a verified real
    client address as request.client before public use; raw X-Forwarded-For is
    deliberately ignored here.
    """
    peer = request.client.host if request.client else None
    if not peer or len(peer) > 255:
        raise ApiError(503, "oauth_unavailable", "Google sign-in is temporarily unavailable.")
    settings = get_settings()
    maximum = (
        settings.oauth_begin_max_per_minute
        if action == "begin"
        else settings.oauth_exchange_max_per_minute
    )
    try:
        async with get_session_factory()() as session:
            async with session.begin():
                now = await session.scalar(select(func.clock_timestamp()))
                expired = (
                    select(OAuthRateLimit.bucket_key)
                    .where(OAuthRateLimit.expires_at < now)
                    .order_by(OAuthRateLimit.expires_at)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
                await session.execute(
                    delete(OAuthRateLimit).where(OAuthRateLimit.bucket_key.in_(expired))
                )
                count, started = await _increment(
                    session, _bucket_key(action, peer), maximum=maximum, now=now
                )
                if count <= maximum:
                    global_count, global_started = await _increment(
                        session,
                        _bucket_key(action, "*"),
                        maximum=settings.oauth_global_max_per_minute,
                        now=now,
                    )
                else:
                    global_count, global_started = 0, now
    except SQLAlchemyError as exc:
        # A missing migration or database outage must never silently disable limits.
        raise ApiError(
            503, "oauth_unavailable", "Google sign-in is temporarily unavailable."
        ) from exc

    if count > maximum or global_count > settings.oauth_global_max_per_minute:
        until = (started if count > maximum else global_started) + _WINDOW
        retry_after = max(1, min(60, math.ceil((until - now).total_seconds())))
        raise ApiError(
            429,
            "oauth_rate_limited",
            "Too many Google sign-in attempts. Try again shortly.",
            headers={
                "Retry-After": str(retry_after),
                "Cache-Control": "no-store",
            },
        )


class OAuthIngressRoute(APIRoute):
    """Read only bounded bodies and count even schema-invalid unauthenticated calls."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            await _bounded_body(request)
            action = _RATE_PATHS.get(request.scope["path"])
            if action:
                await enforce(request, action)
            return await original(request)

        return handler
