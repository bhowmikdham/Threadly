"""Gmail access. RealGmail speaks the REST API directly over httpx (no Google
SDK). MockGmail generates a deterministic inbox so the whole product cycle —
sync, extraction, search, summarise, draft, send — runs with zero credentials."""
import base64
import datetime as dt
import hashlib
from email.message import EmailMessage

import httpx

from . import config, crypto, db

http = httpx.AsyncClient(timeout=30.0)


class GmailError(Exception):
    pass


# ---------------------------------------------------------------- real client

async def exchange_code(code: str) -> dict:
    """OAuth code -> tokens + profile. Returns {email, google_sub?, tokens}."""
    resp = await http.post(config.GOOGLE_TOKEN_URL, data={
        "code": code,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    })
    if resp.status_code != 200:
        raise GmailError(f"token exchange failed: {resp.text}")
    tokens = resp.json()
    profile = await http.get(
        f"{config.GMAIL_API}/users/me/profile",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    if profile.status_code != 200:
        raise GmailError(f"profile fetch failed: {profile.text}")
    return {"email": profile.json()["emailAddress"], "tokens": tokens}


async def _access_token(user_id: int) -> str:
    """Return a live access token, refreshing (and re-storing) if expired."""
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT enc_refresh_token, enc_access_token, access_expires_at FROM oauth_tokens WHERE user_id = $1 AND provider = 'google'",
            user_id,
        )
    if not row or not row["enc_refresh_token"]:
        raise GmailError("user has no Google tokens")

    now = dt.datetime.now(dt.timezone.utc)
    if row["enc_access_token"] and row["access_expires_at"] and row["access_expires_at"] > now + dt.timedelta(seconds=60):
        return crypto.decrypt(row["enc_access_token"])

    resp = await http.post(config.GOOGLE_TOKEN_URL, data={
        "refresh_token": crypto.decrypt(row["enc_refresh_token"]),
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
    })
    if resp.status_code != 200:
        raise GmailError(f"token refresh failed: {resp.text}")
    tokens = resp.json()
    async with db.pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE oauth_tokens SET enc_access_token = $1, access_expires_at = $2, updated_at = now()
            WHERE user_id = $3 AND provider = 'google'
            """,
            crypto.encrypt(tokens["access_token"]),
            now + dt.timedelta(seconds=tokens.get("expires_in", 3600)),
            user_id,
        )
    return tokens["access_token"]


def _walk_for_text(payload: dict) -> str:
    """Depth-first hunt for the text/plain part of a MIME payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"] + "==").decode(errors="replace")
    for part in payload.get("parts", []) or []:
        if text := _walk_for_text(part):
            return text
    return ""


class RealGmail:
    def __init__(self, user_id: int):
        self.user_id = user_id

    async def list_messages(self, after: dt.datetime | None) -> list[dict]:
        """Paginate ALL pages (sync rule) of message metadata + bodies."""
        token = await self._token()
        after = after or dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=config.FIRST_SYNC_LOOKBACK_DAYS)
        query = f"after:{int(after.timestamp())}"
        headers = {"Authorization": f"Bearer {token}"}

        ids: list[str] = []
        page_token = None
        while True:
            params = {"q": query, "maxResults": 100}
            if page_token:
                params["pageToken"] = page_token
            resp = await http.get(f"{config.GMAIL_API}/users/me/messages", params=params, headers=headers)
            if resp.status_code != 200:
                raise GmailError(f"messages.list failed: {resp.text}")
            data = resp.json()
            ids.extend(m["id"] for m in data.get("messages", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break

        out = []
        for msg_id in ids:
            resp = await http.get(f"{config.GMAIL_API}/users/me/messages/{msg_id}", params={"format": "full"}, headers=headers)
            if resp.status_code != 200:
                continue
            msg = resp.json()
            hdrs = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            out.append({
                "gmail_msg_id": msg["id"],
                "gmail_thread_id": msg["threadId"],
                "from_addr": hdrs.get("from", ""),
                "to_addrs": [a.strip() for a in hdrs.get("to", "").split(",") if a.strip()],
                "subject": hdrs.get("subject", ""),
                "sent_at": dt.datetime.fromtimestamp(int(msg["internalDate"]) / 1000, dt.timezone.utc),
                "snippet": msg.get("snippet", ""),
                "body_text": _walk_for_text(msg["payload"]),
                "is_sent": "SENT" in (msg.get("labelIds") or []),
            })
        return out

    async def send(self, to_addrs: list[str], subject: str, body: str) -> str:
        token = await self._token()
        mime = EmailMessage()
        mime["To"] = ", ".join(to_addrs)
        mime["Subject"] = subject or ""
        mime.set_content(body or "")
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        resp = await http.post(
            f"{config.GMAIL_API}/users/me/messages/send",
            json={"raw": raw},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            raise GmailError(f"send failed: {resp.text}")
        return resp.json()["id"]

    async def _token(self) -> str:
        return await _access_token(self.user_id)


# ---------------------------------------------------------------- mock client

def _mock_id(user_id: int, slug: str) -> str:
    return hashlib.sha1(f"{user_id}:{slug}".encode()).hexdigest()[:16]


class MockGmail:
    """Deterministic inbox: GYG orders (incl. one old enough to sit outside the
    default 60-day window, proving the 'want more?' cursor path), a tracking
    email, an invoice, a human thread, and sent mail for RAG."""

    def __init__(self, user_id: int):
        self.user_id = user_id

    async def list_messages(self, after: dt.datetime | None) -> list[dict]:
        now = dt.datetime.now(dt.timezone.utc)
        u = self.user_id

        def gyg_order(days_ago: int, n: int) -> dict:
            return {
                "gmail_msg_id": _mock_id(u, f"gyg-{n}"),
                "gmail_thread_id": _mock_id(u, f"thread-gyg-{n}"),
                "from_addr": "orders@gyg.com.au",
                "to_addrs": ["me@example.com"],
                "subject": f"Your GYG order confirmation #GYG-{84000 + n}",
                "sent_at": now - dt.timedelta(days=days_ago),
                "snippet": f"Thanks for your order GYG-{84000 + n}!",
                "body_text": (
                    f"Thanks for ordering with Guzman y Gomez!\n"
                    f"Order number: GYG-{84000 + n}\nTotal charged: ${9 + n}.50\n"
                    f"Your burrito is on its way."
                ),
                "is_sent": False,
            }

        messages = [
            gyg_order(3, 721), gyg_order(18, 640), gyg_order(45, 512),
            gyg_order(75, 430),   # outside the 60-day default window
            gyg_order(100, 318),  # ditto — cursor page two material
            {
                "gmail_msg_id": _mock_id(u, "ship-1"),
                "gmail_thread_id": _mock_id(u, "thread-ship-1"),
                "from_addr": "noreply@auspost.com.au",
                "to_addrs": ["me@example.com"],
                "subject": "Your package is on its way",
                "sent_at": now - dt.timedelta(days=6),
                "snippet": "Tracking number 33AUS7712345678",
                "body_text": "Your parcel has shipped. Tracking number: 33AUS7712345678. Expected Friday.",
                "is_sent": False,
            },
            {
                "gmail_msg_id": _mock_id(u, "invoice-1"),
                "gmail_thread_id": _mock_id(u, "thread-invoice-1"),
                "from_addr": "billing@hostcorp.io",
                "to_addrs": ["me@example.com"],
                "subject": "Invoice INV-2043 for August",
                "sent_at": now - dt.timedelta(days=10),
                "snippet": "Invoice INV-2043, total $29.00",
                "body_text": "Invoice number: INV-2043\nAmount charged: $29.00\nDue on the 30th.",
                "is_sent": False,
            },
            {
                "gmail_msg_id": _mock_id(u, "boss-1"),
                "gmail_thread_id": _mock_id(u, "thread-boss"),
                "from_addr": "priya@acme-corp.com",
                "to_addrs": ["me@example.com"],
                "subject": "Q3 report draft",
                "sent_at": now - dt.timedelta(days=2, hours=4),
                "snippet": "Can you send the Q3 numbers by Thursday?",
                "body_text": "Hey — can you send me the Q3 numbers by Thursday? Board pack is due Friday.",
                "is_sent": False,
            },
            {
                "gmail_msg_id": _mock_id(u, "me-1"),
                "gmail_thread_id": _mock_id(u, "thread-boss"),
                "from_addr": "me@example.com",
                "to_addrs": ["priya@acme-corp.com"],
                "subject": "Re: Q3 report draft",
                "sent_at": now - dt.timedelta(days=2),
                "snippet": "On it — draft by Wednesday EOD.",
                "body_text": "On it. I'll have a draft over to you by Wednesday EOD.\n\nCheers,\nD",
                "is_sent": True,
            },
            {
                "gmail_msg_id": _mock_id(u, "me-2"),
                "gmail_thread_id": _mock_id(u, "thread-intro"),
                "from_addr": "me@example.com",
                "to_addrs": ["sam@example.org"],
                "subject": "Intro",
                "sent_at": now - dt.timedelta(days=20),
                "snippet": "Great meeting you!",
                "body_text": "Great meeting you at the meetup. Keen to grab coffee next week — Tuesday work?\n\nCheers,\nD",
                "is_sent": True,
            },
        ]
        if after:
            messages = [m for m in messages if m["sent_at"] > after]
        return messages

    async def send(self, to_addrs: list[str], subject: str, body: str) -> str:
        return "mock-" + _mock_id(self.user_id, f"send:{subject}:{body[:50]}")


def client_for(user_id: int, has_google_tokens: bool):
    if config.DEV_MODE and not has_google_tokens:
        return MockGmail(user_id)
    return RealGmail(user_id)
