"""Independent MIME parsing plus real Postgres API, replay and source boundaries."""

import asyncio
import base64
import copy
import hashlib
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

import pytest
from sqlalchemy import func, select, update

from app.actions import email_payload, email_preview
from app.api.errors import ApiError
from app.assistant import draft_review
from app.assistant.context import capture_thread
from app.db.models import (
    ActionApproval,
    ActionAttempt,
    ActionJob,
    ArtifactRevision,
    AssistantAction,
    Message,
    TaskEvent,
    Thread,
    User,
)
from app.schemas.actions import ProposeEmailAction
from tests.conftest import needs_pg
from tests.test_draft_review import edit_request, generated, save
from tests.test_durable_tasks import mailbox

__all__ = ["mailbox"]


def request(key="preview", revision=1):
    return ProposeEmailAction(request_id=key, expected_revision=revision, action_type="send_email")


async def connect(factory, owner):
    async with factory.begin() as session:
        user = await session.get(User, owner)
        user.google_connected = True
        user.google_email_verified = True
        user.google_identity = {"sub": user.google_sub, "email": user.email}
        user.refresh_token_enc = b"unused-by-preview"
        user.google_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]


async def accept(factory, owner, artifact, value=None):
    async with factory.begin() as session:
        return await email_preview.propose(session, owner, artifact.id, value or request())


def decode(action):
    raw = base64.urlsafe_b64decode(action.payload["mime_base64url"])
    assert hashlib.sha256(raw).hexdigest() == action.payload["mime_sha256"]
    return raw, BytesParser(policy=policy.default).parsebytes(raw)


@needs_pg
async def test_edited_preview_round_trip_api_and_no_executor(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    await connect(db_sessionmaker, mailbox[0])
    old = await generated(db_sessionmaker, mailbox[0])
    edited = await save(
        db_sessionmaker,
        mailbox[0],
        old.task_id,
        edit_request(
            subject="Café résumé " * 12,
            body="Bonjour café 👋\r\nSecond line",
            recipients={
                "to": ["NEW@example.test"],
                "cc": ["copy@example.test"],
                "bcc": ["hidden@example.test"],
            },
        ),
    )
    url = f"/assistant/artifacts/{edited.id}/actions"
    response = db_client.post(
        url, json=request(revision=2).model_dump(), headers=auth_headers(mailbox[0])
    )
    assert response.status_code == 201, response.text
    view = response.json()
    assert view["preview"]["to"] == ["new@example.test"]
    assert view["preview"]["bcc"] == ["hidden@example.test"]
    assert view["authorization"] == "none" and not view["approval_available"]
    assert "mime_base64url" not in view and response.headers["cache-control"] == "no-store"
    assert "gmail_send_scope_missing" in view["blockers"]
    replay = db_client.post(
        url, json=request(revision=2).model_dump(), headers=auth_headers(mailbox[0])
    )
    assert replay.json() == view
    assert (
        db_client.get(
            f"/assistant/actions/{view['action_id']}", headers=auth_headers(mailbox[0])
        ).json()
        == view
    )
    async with db_sessionmaker() as session:
        action = await session.get(AssistantAction, view["action_id"])
        raw, mime = decode(action)
        assert not mime.defects and not mime.is_multipart()
        assert mime.get_content_type() == "text/plain"
        assert str(mime["Subject"]) == edited.payload["content"]["subject"]
        assert mime.get_content().replace("\r\n", "\n") == view["preview"]["body"]
        assert [a.addr_spec for a in mime["To"].addresses] == view["preview"]["to"]
        assert [a.addr_spec for a in mime["Cc"].addresses] == view["preview"]["cc"]
        assert [a.addr_spec for a in mime["Bcc"].addresses] == view["preview"]["bcc"]
        assert str(mime["Message-ID"]) == view["preview"]["message_id"]
        assert parsedate_to_datetime(mime["Date"]).tzinfo is not None
        assert all(len(line) <= 998 for line in raw.split(b"\r\n"))
        for model in (ActionJob, ActionApproval, ActionAttempt):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        events = (await session.scalars(select(TaskEvent))).all()
        assert "hidden@example.test" not in str([e.payload for e in events])
        assert "Bonjour" not in str([e.payload for e in events])
    assert (
        db_client.post(
            f"/assistant/actions/{view['action_id']}/approve",
            headers=auth_headers(mailbox[0]),
            json={},
        ).status_code
        == 404
    )


@needs_pg
async def test_concurrent_replay_edit_and_payload_freezing(db_sessionmaker, mailbox):
    await connect(db_sessionmaker, mailbox[0])
    artifact = await generated(db_sessionmaker, mailbox[0])
    first, second = await asyncio.gather(
        *[accept(db_sessionmaker, mailbox[0], artifact) for _ in range(2)]
    )
    assert first.id == second.id and first.payload == second.payload
    original = copy.deepcopy(first.payload)
    edited = await save(db_sessionmaker, mailbox[0], artifact.task_id)
    assert (await accept(db_sessionmaker, mailbox[0], artifact)).payload == original
    new = await accept(db_sessionmaker, mailbox[0], edited, request("new", 2))
    assert new.payload_hash != first.payload_hash
    assert new.payload["preview"]["message_id"] != original["preview"]["message_id"]
    async with db_sessionmaker() as session:
        view = await email_preview.view(session, mailbox[0], first.id)
        assert view["state"] == "superseded" and "revision_superseded" in view["blockers"]
    with pytest.raises(ApiError) as failed:
        await accept(db_sessionmaker, mailbox[0], edited, request("preview", 2))
    assert failed.value.code == "idempotency_conflict"


@needs_pg
async def test_owner_revision_unknown_and_payload_override_rejected(
    db_sessionmaker, mailbox, db_client, auth_headers
):
    await connect(db_sessionmaker, mailbox[0])
    artifact = await generated(db_sessionmaker, mailbox[0])
    action = await accept(db_sessionmaker, mailbox[0], artifact)
    for url in [f"/assistant/actions/{action.id}", "/assistant/actions/missing"]:
        response = db_client.get(url, headers=auth_headers(mailbox[1]))
        assert response.status_code == 404
    response = db_client.post(
        f"/assistant/artifacts/{artifact.id}/actions",
        headers=auth_headers(mailbox[1]),
        json=request().model_dump(),
    )
    assert response.status_code == 404
    for field in ["payload", "to", "body", "user_id", "attachments"]:
        response = db_client.post(
            f"/assistant/artifacts/{artifact.id}/actions",
            headers=auth_headers(mailbox[0]),
            json={**request("new").model_dump(), field: "PRIVATE"},
        )
        assert response.status_code == 422 and "PRIVATE" not in response.text
    with pytest.raises(ApiError) as failed:
        await accept(db_sessionmaker, mailbox[0], artifact, request("stale", 99))
    assert failed.value.code == "revision_conflict"


@needs_pg
async def test_reply_chain_source_binding_and_changed_source(db_sessionmaker, mailbox):
    await connect(db_sessionmaker, mailbox[0])
    artifact = await generated(db_sessionmaker, mailbox[0], reply=True)
    async with db_sessionmaker.begin() as session:
        await session.execute(
            update(Message).values(
                reply_metadata={
                    "schema_version": "1.0",
                    "headers": {
                        "message-id": ["<message@example.test>"],
                        "in-reply-to": ["<parent@example.test>"],
                        "references": ["<root@example.test>\r\n <parent@example.test>"],
                    },
                }
            )
        )
    action = await accept(db_sessionmaker, mailbox[0], artifact)
    _, mime = decode(action)
    assert str(mime["In-Reply-To"]) == "<message@example.test>"
    assert str(mime["References"]).split() == [
        "<root@example.test>",
        "<parent@example.test>",
        "<message@example.test>",
    ]
    assert action.payload["preview"]["gmail_thread_id"] == "thread-one"
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Thread).values(version=Thread.version + 1))
    with pytest.raises(ApiError) as failed:
        await accept(db_sessionmaker, mailbox[0], artifact, request("changed"))
    assert failed.value.code == "email_preview_blocked"
    async with db_sessionmaker() as session:
        assert (
            "source_changed"
            in (await email_preview.view(session, mailbox[0], action.id))["blockers"]
        )
    assert (await accept(db_sessionmaker, mailbox[0], artifact)).id == action.id


@pytest.mark.parametrize(
    "mutation,blocker",
    [
        ("attachments", "attachments_unsupported"),
        ("placeholder", "unresolved_fields"),
        ("alias", "sender_changed"),
        ("duplicates", "draft_content_invalid"),
        ("subject_injection", "draft_content_invalid"),
        ("no_connection", "google_reconnect_required"),
        ("lost_reply", "reply_headers_unavailable"),
    ],
)
@needs_pg
async def test_invalid_candidate_never_persists(db_sessionmaker, mailbox, mutation, blocker):
    await connect(db_sessionmaker, mailbox[0])
    artifact = await generated(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        row = await session.get(ArtifactRevision, artifact.id)
        payload, envelope = copy.deepcopy(row.payload), copy.deepcopy(row.draft_envelope)
        if mutation == "attachments":
            payload["content"]["attachment_refs"] = ["file-one"]
        if mutation == "placeholder":
            payload["content"]["body"] = "Hello [NAME]"
        if mutation == "alias":
            envelope["from_address"] = "alias@example.test"
        if mutation == "duplicates":
            envelope["bcc"] = envelope["to"]
        if mutation == "subject_injection":
            payload["content"]["subject"] = "Hi\r\nBcc: forged@example.test"
        if mutation == "no_connection":
            (await session.get(User, mailbox[0])).google_connected = False
        if mutation == "lost_reply":
            payload["content"]["mode"] = "reply"
        row.payload, row.draft_envelope = payload, envelope
    with pytest.raises(ApiError) as failed:
        await accept(db_sessionmaker, mailbox[0], artifact)
    assert failed.value.detail == {"blockers": [blocker]}
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0


@needs_pg
async def test_account_change_and_expiry_are_read_blockers_not_new_payload(
    db_sessionmaker, mailbox
):
    await connect(db_sessionmaker, mailbox[0])
    artifact = await generated(db_sessionmaker, mailbox[0])
    action = await accept(db_sessionmaker, mailbox[0], artifact)
    async with db_sessionmaker.begin() as session:
        (await session.get(User, mailbox[0])).google_account_version += 1
        (await session.get(AssistantAction, action.id)).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    async with db_sessionmaker() as session:
        view = await email_preview.view(session, mailbox[0], action.id)
        assert {"google_account_changed", "action_expired"} <= set(view["blockers"])
        assert view["preview"] == action.payload["preview"]
    assert (await accept(db_sessionmaker, mailbox[0], artifact)).id == action.id


@pytest.mark.parametrize(
    "values",
    [
        ["<x@example.test>\nBcc: victim@example.test"],
        ["<x@example.test>", "<y@example.test>"],
        ["not an id"],
        ["<x@example.test> trailing"],
        ["<x..y@example.test>"],
        ["<x@é.test>"],
        ["<.x@example.test>"],
        ["<x@-example.test>"],
    ],
)
def test_reference_parser_rejects_unsafe_or_ambiguous_headers(values):
    with pytest.raises(ApiError):
        email_payload.reference_ids(values)


def test_missing_reply_metadata_and_header_limits():
    for value in [None, {}, {"headers": {"message-id": ["<m@x.test>"]}}]:
        with pytest.raises(ApiError):
            email_payload.reply_headers(value, "<m@x.test>")
    with pytest.raises(ApiError):
        email_payload.reference_ids([" ".join(f"<m{i}@x.test>" for i in range(51))])
    assert email_payload.reply_headers(
        {
            "schema_version": "1.0",
            "headers": {
                "message-id": ["<m@x.test>"],
                "references": [],
                "in-reply-to": ["<parent@x.test>"],
            },
        },
        "<m@x.test>",
    ) == ("<m@x.test>", ["<parent@x.test>", "<m@x.test>"])


@needs_pg
async def test_rollback_and_edit_race_leave_no_stale_active_preview(db_sessionmaker, mailbox):
    await connect(db_sessionmaker, mailbox[0])
    artifact = await generated(db_sessionmaker, mailbox[0])
    async with db_sessionmaker() as session:
        await email_preview.propose(session, mailbox[0], artifact.id, request("rollback"))
        await session.rollback()
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0

    async def edit():
        async with db_sessionmaker.begin() as session:
            return await draft_review.edit(session, mailbox[0], artifact.task_id, edit_request())

    results = await asyncio.gather(
        accept(db_sessionmaker, mailbox[0], artifact), edit(), return_exceptions=True
    )
    if isinstance(results[0], ApiError):
        assert results[0].code == "revision_conflict"
    else:
        async with db_sessionmaker() as session:
            assert (await session.get(AssistantAction, results[0].id)).state == "superseded"
    assert not isinstance(results[1], Exception)


@needs_pg
async def test_reply_missing_headers_never_becomes_new_mail(db_sessionmaker, mailbox):
    await connect(db_sessionmaker, mailbox[0])
    artifact = await generated(db_sessionmaker, mailbox[0], reply=True)
    # The legacy fixture has Message-ID only; complete synced headers are mandatory.
    with pytest.raises(ApiError) as failed:
        await accept(db_sessionmaker, mailbox[0], artifact)
    assert failed.value.detail == {"blockers": ["reply_headers_unavailable"]}
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantAction)) == 0


@needs_pg
async def test_compose_checks_artifact_effective_source_after_edit(db_sessionmaker, mailbox):
    await connect(db_sessionmaker, mailbox[0])
    artifact = await generated(db_sessionmaker, mailbox[0])
    async with db_sessionmaker.begin() as session:
        context = await capture_thread(session, mailbox[0], "thread-one")
        row = await session.get(ArtifactRevision, artifact.id)
        row.payload = {**row.payload, "context_snapshot_id": context.id}
    # Models a draft published against continuation context, absent from original task.
    edited = await save(db_sessionmaker, mailbox[0], artifact.task_id)
    action = await accept(db_sessionmaker, mailbox[0], edited, request(revision=2))
    assert context.id in action.source_versions["contexts"]
    async with db_sessionmaker.begin() as session:
        await session.execute(update(Thread).values(version=Thread.version + 1))
    with pytest.raises(ApiError) as failed:
        await accept(db_sessionmaker, mailbox[0], edited, request("stale", 2))
    assert failed.value.detail == {"blockers": ["source_changed"]}
