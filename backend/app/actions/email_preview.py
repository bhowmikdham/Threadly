"""Owned proposal/read seam. Freeze content once; never dispatch from this service."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.actions import email_payload, service
from app.api.errors import ApiError
from app.assistant import draft_review, tasks
from app.assistant.summary import digest
from app.capabilities.service import build_capabilities
from app.db.models import (
    ActionApproval,
    ActionDecision,
    AssistantAction,
    ContextSnapshot,
    Message,
    Thread,
    User,
)


async def account(session, user_id):
    user = await session.get(User, user_id, populate_existing=True)
    if user is None:
        raise ApiError(404, "not_found", "Unknown account.")
    identity = user.google_identity or {}
    if (
        not user.google_connected
        or user.google_email_verified is not True
        or identity.get("sub") != user.google_sub
        or identity.get("email") != user.email
        or not build_capabilities(user)["account"]["credentials_available"]
    ):
        raise email_payload.blocked("google_reconnect_required")
    return user


async def sources(session, task, artifact):
    envelope = artifact.draft_envelope
    if not isinstance(envelope, dict):
        raise email_payload.blocked("draft_envelope_unavailable")
    versions = {}
    # The published artifact binds the effective context (including continuation).
    # User edits retain this field; the task's original context may be superseded.
    context_id = artifact.payload.get("context_snapshot_id")
    context_ids = {context_id} if context_id else set()
    for context_id in sorted(context_ids):
        snapshot = await session.scalar(
            select(ContextSnapshot).where(
                ContextSnapshot.id == context_id, ContextSnapshot.user_id == task.user_id
            )
        )
        if snapshot is None:
            raise email_payload.blocked("source_changed")
        thread = await session.scalar(
            select(Thread)
            .where(Thread.id == snapshot.thread_id, Thread.user_id == task.user_id)
            .execution_options(populate_existing=True)
        )
        if thread is None or thread.version != snapshot.payload.get("thread_version"):
            raise email_payload.blocked("source_changed")
        versions[context_id] = {
            "source_hash": snapshot.source_hash,
            "thread_version": thread.version,
        }
    reply = envelope.get("reply")
    content = artifact.payload.get("content", {})
    if reply is None:
        if (
            envelope.get("reply_message_id") is not None
            or content.get("mode") != "new"
            or content.get("thread_ref") is not None
        ):
            raise email_payload.blocked("reply_headers_unavailable")
        return None, {"contexts": versions}
    if not isinstance(reply, dict) or set(reply) != {
        "gmail_message_id",
        "gmail_thread_id",
        "thread_version",
        "subject",
        "rfc_message_id",
    }:
        raise email_payload.blocked("reply_headers_invalid")
    row = (
        await session.execute(
            select(Message, Thread)
            .join(Thread, Thread.id == Message.thread_id)
            .where(
                Message.user_id == task.user_id,
                Thread.user_id == task.user_id,
                Message.gmail_msg_id == reply["gmail_message_id"],
                Thread.gmail_thread_id == reply["gmail_thread_id"],
            )
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None or row.Thread.version != reply["thread_version"]:
        raise email_payload.blocked("reply_context_changed")
    message = row.Message
    if (
        envelope.get("reply_message_id") != message.gmail_msg_id
        or content.get("mode") != "reply"
        or content.get("thread_ref") != reply["gmail_thread_id"]
        or artifact.payload.get("content", {}).get("subject") != reply["subject"]
        or not message.subject
    ):
        raise email_payload.blocked("reply_context_changed")
    expected = (
        message.subject
        if message.subject.casefold().startswith("re:")
        else "Re: " + message.subject
    )
    if expected != reply["subject"]:
        raise email_payload.blocked("reply_context_changed")
    mid, refs = email_payload.reply_headers(message.reply_metadata, reply["rfc_message_id"])
    return {"in_reply_to": mid, "references": refs, "gmail_thread_id": reply["gmail_thread_id"]}, {
        "contexts": versions,
        "reply_thread_version": row.Thread.version,
        "reply_metadata_hash": digest(message.reply_metadata),
    }


def request_identity(artifact_id, request):
    return {
        "artifact_id": artifact_id,
        "expected_revision": request.expected_revision,
        "action_type": request.action_type,
    }


async def replay(session, user_id, artifact_id, request):
    existing = await session.scalar(
        select(AssistantAction).where(
            AssistantAction.user_id == user_id,
            AssistantAction.proposal_request_id == request.request_id,
        )
    )
    if existing is None:
        return None
    if existing.payload_schema != email_payload.SCHEMA or existing.source_versions.get(
        "proposal_request"
    ) != request_identity(artifact_id, request):
        raise ApiError(409, "idempotency_conflict", "Proposal key was used for different input.")
    return existing


async def propose(session, user_id, artifact_id, request):
    previous = await replay(session, user_id, artifact_id, request)
    if previous:
        return previous
    artifact = await draft_review.owned_artifact(session, user_id, artifact_id)
    task = await tasks.owned_task(session, user_id, artifact.task_id, lock=True)
    await session.refresh(task)
    previous = await replay(session, user_id, artifact_id, request)
    if previous:
        return previous
    draft_review.require_draft(artifact)
    if (
        task.state != "succeeded"
        or task.final_artifact_id != artifact.id
        or artifact.revision != request.expected_revision
    ):
        raise ApiError(409, "revision_conflict", "Use the current completed draft.")
    user = await account(session, user_id)
    reply, source_versions = await sources(session, task, artifact)
    now = await session.scalar(select(func.clock_timestamp()))
    payload = email_payload.build(
        artifact.draft_envelope,
        artifact.payload["content"],
        sender=user.email,
        now=now,
        identifier=str(uuid4()),
        reply=reply,
    )
    source_versions.update(
        proposal_request=request_identity(artifact_id, request),
        google_account_version=user.google_account_version,
        google_subject=user.google_sub,
        google_scopes=user.google_scopes,
    )
    return await service.propose(
        session,
        user_id,
        artifact_id,
        request_id=request.request_id,
        expected_revision=request.expected_revision,
        action_type="send_email",
        payload_schema=email_payload.SCHEMA,
        payload=payload,
        source_versions=source_versions,
        expires_at=now + timedelta(minutes=30),
    )


async def view(session, user_id, action_id):
    task, action = await service.owned_action(session, user_id, action_id, lock=True)
    if action.payload_schema != email_payload.SCHEMA:
        raise ApiError(
            409, "action_preview_unavailable", "This action has no supported email preview."
        )
    blockers = ["send_executor_unavailable"]
    if action.state != "proposed":
        blockers.append("action_" + action.state)
    if action.expires_at <= await session.scalar(select(func.clock_timestamp())):
        blockers.append("action_expired")
    artifact = await draft_review.owned_artifact(session, user_id, action.artifact_id)
    if task.final_artifact_id != artifact.id:
        blockers.append("revision_superseded")
    if action.source_artifact_hash != digest(
        {"artifact": artifact.payload, "envelope": artifact.draft_envelope}
    ):
        blockers.append("artifact_changed")
    try:
        user = await account(session, user_id)
        if (
            user.google_account_version != action.source_versions["google_account_version"]
            or user.google_sub != action.source_versions["google_subject"]
            or user.google_scopes != action.source_versions["google_scopes"]
        ):
            blockers.append("google_account_changed")
        caps = {c["id"]: c for c in build_capabilities(user)["capabilities"]}
        if caps["gmail_send"]["scope_status"] != "granted":
            blockers.append("gmail_send_scope_" + caps["gmail_send"]["scope_status"])
        _, current = await sources(session, task, artifact)
        if any(action.source_versions.get(key) != value for key, value in current.items()):
            blockers.append("source_changed")
    except ApiError as error:
        if error.code != "email_preview_blocked":
            raise
        blockers.extend(error.detail["blockers"])
    approval_id = await session.scalar(
        select(ActionApproval.id).where(
            ActionApproval.action_id == action.id, ActionApproval.user_id == user_id
        )
    )
    cancellation_requested = (
        await session.scalar(
            select(ActionDecision.id)
            .where(
                ActionDecision.action_id == action.id,
                ActionDecision.user_id == user_id,
                ActionDecision.decision == "cancellation_requested",
            )
            .limit(1)
        )
        is not None
    )
    allowed = []
    if action.state == "proposed":
        allowed.append("reject")
    if (
        action.state in {"proposed", "approved", "executing", "outcome_unknown"}
        and not cancellation_requested
    ):
        allowed.append("cancel")
    return {
        "action_id": action.id,
        "task_id": action.task_id,
        "artifact_id": action.artifact_id,
        "action_type": "send_email",
        "state": action.state,
        "version": action.version,
        "payload_schema": action.payload_schema,
        "payload_hash": action.payload_hash,
        "mime_sha256": action.payload["mime_sha256"],
        "expires_at": action.expires_at.isoformat(),
        "preview": action.payload["preview"],
        "account_version": action.source_versions["google_account_version"],
        "blockers": list(dict.fromkeys(blockers)),
        "approval_available": False,
        "sending_available": False,
        "authorization": "exact_payload_approval" if approval_id else "none",
        "approval_id": approval_id,
        "cancellation_requested": cancellation_requested,
        "allowed_operations": allowed,
        "result": action.result,
        "error_code": action.error_code,
    }
