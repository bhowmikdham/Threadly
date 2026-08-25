"""Gmail API over plain REST (module 3). httpx only — no google SDK in the image.

Every list call paginates ALL pages (nextPageToken loop) — the architecture
doc's non-negotiable. Batched detail fetches keep the backfill reasonable.
"""
import asyncio
import base64
from typing import Any

import httpx

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_PAGE_SIZE = 100
_DETAIL_CONCURRENCY = 8


class GmailError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GmailClient:
    def __init__(self, access_token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30, transport=self._transport, headers=self._headers)

    async def _get(self, client: httpx.AsyncClient, url: str, params: dict) -> dict:
        r = await client.get(url, params=params)
        if r.status_code == 401:
            raise GmailError("gmail auth expired", 401)
        if r.status_code == 404:
            raise GmailError("not found", 404)
        if r.status_code != 200:
            raise GmailError(f"gmail api error: {r.text[:200]}", r.status_code)
        return r.json()

    async def list_all_message_ids(self, query: str | None = None) -> list[dict[str, str]]:
        """EVERY page of messages.list -> [{"id", "threadId"}]. Never trusts one page."""
        out: list[dict[str, str]] = []
        params: dict[str, Any] = {"maxResults": _PAGE_SIZE}
        if query:
            params["q"] = query
        async with self._client() as client:
            while True:
                body = await self._get(client, f"{BASE}/messages", params)
                out.extend(body.get("messages", []))
                token = body.get("nextPageToken")
                if not token:
                    return out
                params["pageToken"] = token

    async def get_messages_full(self, ids: list[str]) -> list[dict]:
        """Fetch message details with bounded concurrency, preserving input order."""
        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        async with self._client() as client:

            async def one(mid: str) -> dict:
                async with sem:
                    return await self._get(client, f"{BASE}/messages/{mid}", {"format": "full"})

            return list(await asyncio.gather(*(one(m) for m in ids)))

    async def get_profile(self) -> dict:
        async with self._client() as client:
            return await self._get(client, f"{BASE}/profile", {})

    async def history_since(self, history_id: str) -> tuple[list[str], str | None]:
        """All pages of history.list -> (changed message ids, newest historyId).
        Raises GmailError(404) when the cursor expired => caller falls back to backfill."""
        changed: list[str] = []
        newest: str | None = None
        params: dict[str, Any] = {"startHistoryId": history_id, "maxResults": _PAGE_SIZE}
        async with self._client() as client:
            while True:
                body = await self._get(client, f"{BASE}/history", params)
                newest = body.get("historyId", newest)
                for h in body.get("history", []):
                    for added in h.get("messagesAdded", []):
                        mid = added.get("message", {}).get("id")
                        if mid:
                            changed.append(mid)
                token = body.get("nextPageToken")
                if not token:
                    return changed, newest
                params["pageToken"] = token


# ---------------------------------------------------------------- payload parsing


def _b64url(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_body_text(payload: dict) -> str:
    """Walk the MIME tree; prefer text/plain parts, fall back to stripped text/html."""
    plains: list[str] = []
    htmls: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            if mime == "text/plain":
                plains.append(_b64url(data))
            elif mime == "text/html":
                htmls.append(_b64url(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if plains:
        return "\n".join(plains)
    if htmls:
        return strip_html("\n".join(htmls))
    return ""


def strip_html(html: str) -> str:
    import re

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def header(payload: dict, name: str) -> str | None:
    for h in payload.get("headers", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value")
    return None
