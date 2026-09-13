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
        try:
            r = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise GmailError("gmail request failed") from exc
        if r.status_code == 401:
            raise GmailError("gmail auth expired", 401)
        if r.status_code == 404:
            raise GmailError("not found", 404)
        if r.status_code != 200:
            raise GmailError("gmail api error", r.status_code)
        try:
            body = r.json()
        except ValueError as exc:
            raise GmailError("invalid gmail response") from exc
        if not isinstance(body, dict):
            raise GmailError("invalid gmail response")
        return body

    async def list_all_message_ids(self, query: str | None = None) -> list[dict[str, str]]:
        """EVERY page of messages.list -> [{"id", "threadId"}]. Never trusts one page."""
        out: list[dict[str, str]] = []
        seen_pages: set[str] = set()
        params: dict[str, Any] = {"maxResults": _PAGE_SIZE}
        if query:
            params["q"] = query
        async with self._client() as client:
            while True:
                body = await self._get(client, f"{BASE}/messages", params)
                out.extend(body.get("messages", []))
                token = body.get("nextPageToken")
                if not token:
                    return list({m["id"]: m for m in out}.values())
                if token in seen_pages:
                    raise GmailError("repeated gmail page token")
                seen_pages.add(token)
                params["pageToken"] = token

    async def get_messages_full(
        self, ids: list[str], *, ignore_missing: bool = False
    ) -> list[dict]:
        """Fetch message details with bounded concurrency, preserving input order."""
        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)
        async with self._client() as client:

            async def one(mid: str) -> dict | None:
                async with sem:
                    try:
                        result = await self._get(
                            client, f"{BASE}/messages/{mid}", {"format": "full"}
                        )
                    except GmailError as exc:
                        if ignore_missing and exc.status == 404:
                            return None
                        raise
                    if result.get("id") != mid or not result.get("threadId"):
                        raise GmailError("invalid gmail message identity")
                    return result

            results = []
            unique = list(dict.fromkeys(ids))
            # Bound task creation as well as active HTTP requests on large mailboxes.
            for offset in range(0, len(unique), _DETAIL_CONCURRENCY):
                batch = await asyncio.gather(
                    *(one(m) for m in unique[offset : offset + _DETAIL_CONCURRENCY])
                )
                results.extend(m for m in batch if m is not None)
            return results

    async def get_profile(self) -> dict:
        async with self._client() as client:
            return await self._get(client, f"{BASE}/profile", {})

    async def history_since(self, history_id: str) -> tuple[list[str], str | None]:
        """All pages -> deduplicated IDs affected by adds, deletes or label changes.
        Callers fetch current details; a detail 404 represents a removed message.
        Raises GmailError(404) when the cursor expired => caller falls back to backfill."""
        changed: list[str] = []
        seen_pages: set[str] = set()
        newest: str | None = None
        params: dict[str, Any] = {"startHistoryId": history_id, "maxResults": _PAGE_SIZE}
        async with self._client() as client:
            while True:
                body = await self._get(client, f"{BASE}/history", params)
                newest = body.get("historyId", newest)
                for h in body.get("history", []):
                    for kind in (
                        "messagesAdded",
                        "messagesDeleted",
                        "labelsAdded",
                        "labelsRemoved",
                    ):
                        for change in h.get(kind, []):
                            mid = change.get("message", {}).get("id")
                            if mid:
                                changed.append(mid)
                token = body.get("nextPageToken")
                if not token:
                    if not newest or not str(newest).isdigit() or int(newest) < int(history_id):
                        raise GmailError("invalid gmail history cursor")
                    return list(dict.fromkeys(changed)), str(newest)
                if token in seen_pages:
                    raise GmailError("repeated gmail history page token")
                seen_pages.add(token)
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
