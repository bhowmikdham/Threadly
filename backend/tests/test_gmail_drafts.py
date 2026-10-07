"""Gmail draft-only writes with fake HTTP, exact MIME and real database races."""

import asyncio
import base64
import copy
import json
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.actions import gmail_draft
from app.api.errors import ApiError
from app.conversation import store
from app.db.models import ActionApproval, AssistantAction, Conversation, GmailDraftSave, User
from app.schemas.gmail_draft import CreateGmailDraft
from tests.conftest import needs_pg
from tests.test_draft_review import generated, save
from tests.test_durable_tasks import mailbox
from tests.test_email_previews import connect

__all__ = ["mailbox"]


@pytest.fixture
def token(monkeypatch):
    async def fake(*args, **kwargs):
        return "synthetic-token"

    monkeypatch.setattr(gmail_draft, "get_valid_access_token", fake)


def request(artifact, **changes):
    return CreateGmailDraft(
        **{
            "request_id": "click-1",
            "artifact_id": artifact.id,
            "expected_revision": artifact.revision,
            "from_address": "first@example.test",
            "account_version": 1,
            "subject": "Edited café",
            "body": "  Exact edits 👋\r\nNo terminal newline  ",
            "recipients": {
                "to": ["new@example.test"],
                "cc": ["cc@example.test"],
                "bcc": ["private@example.test"],
            },
            "unresolved_fields": [],
            **changes,
        }
    )


async def ready(factory, owner, scope="gmail.compose"):
    await connect(factory, owner)
    async with factory.begin() as session:
        user = await session.get(User, owner)
        user.google_scopes = [f"https://www.googleapis.com/auth/{scope}"]


def provider(calls, state="success"):
    async def handle(req):
        calls.append(req)
        assert req.method == "POST" and str(req.url) == gmail_draft.DRAFT_URL
        assert req.headers["Authorization"] == "Bearer synthetic-token"
        if state == "timeout":
            raise httpx.ReadTimeout("lost response")
        if state == "rejected":
            return httpx.Response(403, json={"error": {"code": 403}})
        return httpx.Response(
            200,
            json={
                "id": "draft-1",
                "message": {"id": "message-1", "threadId": "thread-1", "labelIds": ["DRAFT"]},
            },
        )

    return httpx.MockTransport(handle)


async def create(factory, owner, body, transport):
    async with factory() as session:
        return await gmail_draft.create(session, owner, body, transport=transport)


@needs_pg
async def test_exact_editor_only_creates_draft_and_deduplicates(db_sessionmaker, mailbox, token):
    owner = mailbox[0]
    await ready(db_sessionmaker, owner)
    artifact = await generated(db_sessionmaker, owner)
    before = copy.deepcopy(artifact.payload)
    calls = []
    body = request(artifact)
    result = await create(db_sessionmaker, owner, body, provider(calls))
    assert result["state"] == "succeeded" and not result["sending_available"]
    mime = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(json.loads(calls[0].content)["message"]["raw"])
    )
    assert str(mime["Subject"]) == body.subject
    assert mime.get_payload(decode=True).decode("utf-8") == body.body
    assert str(mime["To"]) == "new@example.test"
    assert str(mime["Cc"]) == "cc@example.test"
    assert str(mime["Bcc"]) == "private@example.test"
    assert set(json.loads(calls[0].content)) == {"message"}
    for key in ("click-1", "another-tab"):
        replay = await create(
            db_sessionmaker, owner, request(artifact, request_id=key), provider(calls)
        )
        assert replay["save_id"] == result["save_id"]
    assert len(calls) == 1
    async with db_sessionmaker() as session:
        row = await session.scalar(select(GmailDraftSave))
        assert row.provenance["parent_artifact_id"] == artifact.id
        assert row.payload["preview"]["body"] == body.body
        assert artifact.payload == before
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0
        assert await session.scalar(select(func.count()).select_from(ActionApproval)) == 0
    with pytest.raises(ApiError) as error:
        await create(db_sessionmaker, owner, request(artifact, body="changed"), provider(calls))
    assert error.value.code == "idempotency_conflict"


@needs_pg
async def test_concurrent_clicks_create_once(db_sessionmaker, mailbox, token):
    owner = mailbox[0]
    await ready(db_sessionmaker, owner)
    artifact = await generated(db_sessionmaker, owner)
    calls = []
    values = await asyncio.gather(
        *[
            create(
                db_sessionmaker, owner, request(artifact, request_id=f"tab-{i}"), provider(calls)
            )
            for i in range(2)
        ]
    )
    assert values[0]["save_id"] == values[1]["save_id"]
    assert len(calls) == 1


@needs_pg
@pytest.mark.parametrize("state", ["timeout", "rejected"])
async def test_failed_or_uncertain_dispatch_never_retries(db_sessionmaker, mailbox, token, state):
    owner = mailbox[0]
    await ready(db_sessionmaker, owner)
    artifact = await generated(db_sessionmaker, owner)
    calls = []
    result = await create(db_sessionmaker, owner, request(artifact), provider(calls, state))
    assert result["state"] == ("outcome_unknown" if state == "timeout" else "failed")
    await create(db_sessionmaker, owner, request(artifact), provider(calls))
    assert len(calls) == 1


@needs_pg
async def test_scope_owner_account_and_revision_fences(db_sessionmaker, mailbox, token):
    owner, other = mailbox[:2]
    await ready(db_sessionmaker, owner, "gmail.send")
    artifact = await generated(db_sessionmaker, owner)
    calls = []
    with pytest.raises(ApiError) as error:
        await create(db_sessionmaker, owner, request(artifact), provider(calls))
    assert error.value.code == "gmail_draft_permission_required"
    await ready(db_sessionmaker, owner)
    for changes in (
        {"account_version": 2},
        {"from_address": "other@example.test"},
        {"expected_revision": 2},
    ):
        with pytest.raises(ApiError):
            await create(db_sessionmaker, owner, request(artifact, **changes), provider(calls))
    await ready(db_sessionmaker, other)
    with pytest.raises(ApiError) as error:
        await create(
            db_sessionmaker,
            other,
            request(artifact, from_address="second@example.test"),
            provider(calls),
        )
    assert error.value.status == 404
    await save(db_sessionmaker, owner, artifact.task_id)
    with pytest.raises(ApiError) as error:
        await create(db_sessionmaker, owner, request(artifact), provider(calls))
    assert error.value.code == "revision_conflict"
    assert not calls


@needs_pg
async def test_named_recipient_requires_address_and_current_owned_chat(
    db_sessionmaker, mailbox, token
):
    owner = mailbox[0]
    await ready(db_sessionmaker, owner)
    cid, did = str(uuid4()), str(uuid4())
    source = {
        "draft_id": did,
        "recipient": "Alex",
        "subject": "Thanks",
        "body": "Thank you.",
        "unresolved_fields": [],
    }
    async with db_sessionmaker.begin() as session:
        session.add(
            Conversation(
                id=cid,
                user_id=owner,
                account_version=1,
                version=1,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                state_enc=store.encode(
                    {
                        "history": [{"email_draft": source}],
                        "email_draft_goal": {"status": "drafted"},
                    }
                ),
            )
        )
    values = dict(
        request_id="named-click",
        artifact_id=None,
        draft_id=did,
        conversation_id=cid,
        expected_version=1,
        expected_revision=1,
        from_address="first@example.test",
        account_version=1,
        subject="My edited thanks",
        body="My edits",
        recipients={"to": ["alex@example.test"]},
        unresolved_fields=[],
    )
    with pytest.raises(ValueError):
        CreateGmailDraft(**{**values, "recipients": {"to": ["Alex"]}})
    calls = []
    with pytest.raises(ApiError) as error:
        await create(
            db_sessionmaker,
            owner,
            CreateGmailDraft(**{**values, "expected_version": 2}),
            provider(calls),
        )
    assert error.value.code == "conversation_version_conflict"
    result = await create(db_sessionmaker, owner, CreateGmailDraft(**values), provider(calls))
    assert result["state"] == "succeeded" and len(calls) == 1


@needs_pg
async def test_changed_reply_subject_drops_thread_binding(db_sessionmaker, mailbox, token):
    owner = mailbox[0]
    await ready(db_sessionmaker, owner)
    artifact = await generated(db_sessionmaker, owner, reply=True)
    # Existing fixtures may omit RFC metadata; source validation stays mandatory.
    from app.db.models import Message

    async with db_sessionmaker.begin() as session:
        message = await session.scalar(
            select(Message).where(
                Message.gmail_msg_id == artifact.draft_envelope["reply_message_id"]
            )
        )
        message.reply_metadata = {
            "schema_version": "1.0",
            "headers": {
                "message-id": [artifact.draft_envelope["reply"]["rfc_message_id"]],
                "references": [],
                "in-reply-to": [],
            },
        }
    calls = []
    await create(db_sessionmaker, owner, request(artifact), provider(calls))
    assert "threadId" not in json.loads(calls[0].content)["message"]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"id": "draft", "message": {"id": "m", "labelIds": ["INBOX"]}},
        {"id": "draft", "message": {"id": "m", "labelIds": None}},
    ],
)
async def test_malformed_provider_success_stays_uncertain(response):
    result = await gmail_draft.create_provider(
        "synthetic-token",
        {"mime_base64url": "eA==", "preview": {"gmail_thread_id": None}},
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    )
    assert result[0] == "outcome_unknown"


@needs_pg
async def test_api_reads_and_click_contract(
    db_sessionmaker, mailbox, db_client, auth_headers, token, monkeypatch
):
    owner = mailbox[0]
    await ready(db_sessionmaker, owner)
    artifact = await generated(db_sessionmaker, owner)
    calls = []
    original = gmail_draft.create_provider

    async def synthetic(token, payload, **kwargs):
        return await original(token, payload, transport=provider(calls))

    monkeypatch.setattr(gmail_draft, "create_provider", synthetic)
    headers = auth_headers(owner)
    path = f"/assistant/gmail-drafts/task/{artifact.task_id}"
    assert db_client.get(path, headers=headers).json() is None
    bad = db_client.post(
        "/assistant/gmail-drafts",
        json={**request(artifact).model_dump(), "send": True},
        headers=headers,
    )
    assert bad.status_code == 422 and not calls
    result = db_client.post(
        "/assistant/gmail-drafts", json=request(artifact).model_dump(), headers=headers
    )
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["state"] == "succeeded"
    assert db_client.get(path, headers=headers).json()["save_id"] == result.json()["save_id"]
    assert db_client.get(path, headers=auth_headers(mailbox[1])).json() is None
    assert len(calls) == 1


@needs_pg
async def test_account_change_during_token_refresh_stops_before_write(
    db_sessionmaker, mailbox, monkeypatch
):
    owner = mailbox[0]
    await ready(db_sessionmaker, owner)
    artifact = await generated(db_sessionmaker, owner)

    async def changed(*args, **kwargs):
        async with db_sessionmaker.begin() as session:
            user = await session.get(User, owner)
            user.google_account_version += 1
        return "synthetic-token"

    monkeypatch.setattr(gmail_draft, "get_valid_access_token", changed)
    calls = []
    with pytest.raises(ApiError) as error:
        await create(db_sessionmaker, owner, request(artifact), provider(calls))
    assert error.value.code == "google_connection_changed" and not calls


def test_long_structured_draft_compaction_keeps_exact_text():
    from app.conversation.email_draft import model_context

    draft = {"subject": "Long draft", "body": "a" * 20000, "unresolved_fields": [], "sources": []}
    public = {"draft_id": "draft", "recipient": "Alex", **draft}
    state = {
        "history": [{"assistant": draft["body"], "email_draft": public}],
        "email_draft_goal": {"status": "drafted", "draft": draft},
        "receipts": [
            {"request_id": "current", "response": {"text": draft["body"], "email_draft": public}}
        ],
    }
    store.compact(state, preserve_receipt_id="current")
    assert state["history"][-1]["email_draft"]["body"] == draft["body"]
    assert model_context(state)["draft"]["body"] == draft["body"]
    assert state["receipts"][0]["response"]["email_draft"]["body"] == draft["body"]
