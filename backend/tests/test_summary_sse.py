"""W1 exit criterion: /summary streams over SSE, persists, and caches."""
import json

import pytest

from tests.conftest import needs_pg
from tests.test_gmail_client import gmail_transport

pytestmark = needs_pg


class FakeModelClient:
    async def stream(self, prompt: str, *, small: bool = False, max_tokens: int = 700):
        async def gen():
            for t in ("Team agreed ", "to ship Friday."):
                yield t

        from app.model_client.client import GenResult

        return gen(), GenResult("fake", "fake-model")


def _events(resp_text: str) -> list[tuple[str, dict]]:
    out = []
    event = None
    for line in resp_text.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ").strip()
        elif line.startswith("data: ") and event:
            out.append((event, json.loads(line.removeprefix("data: "))))
    return out


@pytest.fixture()
def synced_user(db_sessionmaker):
    import asyncio

    from app.auth import crypto
    from app.db import repositories as repo
    from app.sync import worker

    async def seed():
        from datetime import UTC, datetime

        async with db_sessionmaker() as session:
            user = await repo.upsert_user(
                session,
                google_sub="g1",
                email="me@x.com",
                display_name="Me",
                access_token_enc=crypto.encrypt_token("at"),
                access_token_expires_at=datetime.now(UTC),
                refresh_token_enc=crypto.encrypt_token("rt"),
            )
            await session.commit()
            await worker.initial_backfill(session, user, "tok", transport=gmail_transport())
            return user.id

    return asyncio.run(seed())


def test_summary_streams_then_caches(db_client, auth_headers, synced_user, monkeypatch):
    import app.orchestrator.orchestrator as orch

    monkeypatch.setattr(orch, "get_model_client", lambda: FakeModelClient())

    r = db_client.get("/threads/t1/summary", headers=auth_headers(synced_user))
    assert r.status_code == 200
    events = _events(r.text)
    kinds = [k for k, _ in events]
    assert kinds[-1] == "done"
    text = "".join(d["text"] for k, d in events if k == "token")
    assert text == "Team agreed to ship Friday."

    # second call: cache hit — single token, provider=cache, no model involved
    r2 = db_client.get("/threads/t1/summary", headers=auth_headers(synced_user))
    events2 = _events(r2.text)
    assert [k for k, _ in events2] == ["token", "done"]
    assert events2[0][1]["text"] == "Team agreed to ship Friday."
    assert events2[1][1]["provider"] == "cache"


def test_summary_unknown_thread_emits_error_event(db_client, auth_headers, synced_user):
    r = db_client.get("/threads/nope/summary", headers=auth_headers(synced_user))
    events = _events(r.text)
    assert events[0][0] == "error"
    assert events[0][1]["error"]["code"] == "not_found"
