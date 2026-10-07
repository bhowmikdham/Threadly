"""Draft-only Gmail persistence. No send approval, job, endpoint or retry path.

The exact editor is frozen and committed before HTTP. One receipt per source
prevents duplicate creation even after a lost response, restart or new request ID.
An uncertain dispatch stays uncertain and must be inspected in Gmail.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
from sqlalchemy import or_, select

from app.actions import email_payload, email_preview
from app.actions.gmail_sender import ID
from app.api.errors import ApiError
from app.assistant import draft_review, tasks
from app.assistant.summary import digest
from app.auth.service import get_valid_access_token
from app.capabilities.service import build_capabilities
from app.conversation import store
from app.db.models import GmailDraftSave, User
from app.model_client.structured import reject_duplicate_keys

DRAFT_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"


def view(row):
    state = row.state
    if state == "saving" and row.created_at < datetime.now(UTC) - timedelta(seconds=90):
        state = "outcome_unknown"
    return {
        "save_id": row.id,
        "source_artifact_id": row.provenance.get("parent_artifact_id"),
        "source_revision": row.provenance.get("parent_revision"),
        "state": state,
        "preview": row.payload["preview"],
        "error_code": row.error_code,
        "result": row.result,
        "sending_available": False,
    }


async def lookup(session, owner, kind, identifier):
    row = await session.scalar(
        select(GmailDraftSave).where(
            GmailDraftSave.user_id == owner,
            GmailDraftSave.source_key == f"{kind}:{identifier}",
        )
    )
    if row:
        user = await session.get(User, owner, populate_existing=True)
        if not user or user.google_account_version != row.account_version:
            raise ApiError(409, "google_connection_changed", "Your Google connection changed.")
    return view(row) if row else None


async def account(session, owner, request):
    user = await email_preview.account(session, owner)
    if user.email != request.from_address or user.google_account_version != request.account_version:
        raise ApiError(
            409, "google_connection_changed", "Your Google connection changed. Reload the draft."
        )
    capability = next(
        c for c in build_capabilities(user)["capabilities"] if c["id"] == "gmail_draft"
    )
    if not capability["ready"]:
        raise ApiError(
            403,
            "gmail_draft_permission_required",
            "This connection cannot save Gmail drafts. You can still edit and copy the text.",
        )
    return user


async def create(session, owner, request, *, transport=None):
    # Replay before any token refresh or source hydration. Changed content with
    # the same key is never treated as the original successful save.
    hashed = digest(request.model_dump(exclude={"request_id"}))
    previous = await session.scalar(
        select(GmailDraftSave).where(
            GmailDraftSave.user_id == owner,
            GmailDraftSave.request_id == request.request_id,
        )
    )
    if previous:
        if previous.request_hash != hashed:
            raise ApiError(
                409, "idempotency_conflict", "That save attempt contains different edits."
            )
        await account(session, owner, request)
        return view(previous)
    user = await account(session, owner, request)
    session_version = user.threadly_session_version
    await session.rollback()  # Token refresh owns a separate short transaction.
    token = await get_valid_access_token(session, SimpleNamespace(id=owner), transport=transport)
    await session.get(User, owner, with_for_update=True, populate_existing=True)
    user = await account(session, owner, request)
    if user.threadly_session_version != session_version:
        raise ApiError(409, "google_connection_changed", "Your session changed. Reload the draft.")
    conversation = None
    state = None
    if request.conversation_id:
        conversation = await store.owned(session, owner, request.conversation_id, lock=True)
        state = store.decode(conversation)
        if conversation.version != request.expected_version or conversation.pending_request_id:
            raise ApiError(
                409,
                "conversation_version_conflict",
                "This chat changed. Review the latest draft before creating it.",
            )
    reply = None
    if request.artifact_id:
        artifact = await draft_review.owned_artifact(session, owner, request.artifact_id)
        task = await tasks.owned_task(session, owner, artifact.task_id, lock=True)
        await session.refresh(task)
        source_key = f"task:{task.id}"
        provenance = {
            "source": "user_edit",
            "parent_artifact_id": artifact.id,
            "parent_revision": artifact.revision,
            "parent_hash": draft_review.payload_hash(artifact),
        }
        envelope = dict(artifact.draft_envelope or {})
    else:
        source_key = f"conversation:{request.draft_id}"
        provenance = {
            "source": "user_edit",
            "conversation_id": conversation.id,
            "conversation_version": conversation.version,
            "draft_id": request.draft_id,
        }
        envelope = {"from_address": user.email, "reply": None, "reply_message_id": None}
    previous = await session.scalar(
        select(GmailDraftSave).where(
            GmailDraftSave.user_id == owner,
            or_(
                GmailDraftSave.source_key == source_key,
                GmailDraftSave.request_id == request.request_id,
            ),
        )
    )
    if previous:
        if previous.request_hash != hashed:
            raise ApiError(
                409,
                "draft_already_submitted",
                "This draft already has a save attempt. Refresh its status; edit it in Gmail.",
            )
        return view(previous)
    if request.artifact_id:
        draft_review.require_draft(artifact)
        if (
            task.state != "succeeded"
            or task.final_artifact_id != artifact.id
            or artifact.revision != request.expected_revision
        ):
            raise ApiError(
                409, "revision_conflict", "This draft changed. Load its latest revision."
            )
        if state is not None and state.get("active_task_id") != task.id:
            raise ApiError(
                409, "conversation_version_conflict", "This is no longer the active draft."
            )
        if await email_preview.unresolved_send(session, owner, task.id):
            raise ApiError(
                409,
                "previous_send_unresolved",
                "An earlier send is unresolved. Check its status first.",
            )
        reply, versions = await email_preview.sources(session, task, artifact)
        provenance["source_versions"] = versions
        # Gmail requires a matching subject to retain thread identity. A deliberate
        # subject edit creates a new email, with the original source still recorded.
        if request.subject != artifact.payload["content"]["subject"] and reply:
            reply = None
            envelope.update(reply=None, reply_message_id=None)
    else:
        source = next(
            (
                h.get("email_draft")
                for h in state["history"]
                if (h.get("email_draft") or {}).get("draft_id") == request.draft_id
            ),
            None,
        )
        from app.conversation.email_draft import current_text_draft

        goal = state.get("email_draft_goal") or {}
        if (
            source is None
            or goal.get("status") != "drafted"
            or source != current_text_draft(state)
        ):
            raise ApiError(
                409, "draft_source_changed", "Review the latest generated email before saving it."
            )
        provenance["parent_hash"] = digest(source)
    envelope.update(request.recipients.model_dump(exclude={"reply_message_id"}))
    identifier = str(uuid4())
    payload = email_payload.build(
        envelope,
        {
            "subject": request.subject,
            "body": request.body,
            "unresolved_fields": request.unresolved_fields,
        },
        sender=user.email,
        now=datetime.now(UTC),
        identifier=identifier,
        reply=reply,
        draft_only=True,
    )
    row = GmailDraftSave(
        id=identifier,
        user_id=owner,
        source_key=source_key,
        request_id=request.request_id,
        request_hash=hashed,
        account_version=user.google_account_version,
        state="saving",
        payload=payload,
        provenance=provenance,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    await session.commit()  # Dispatch cutoff. Never retry this HTTP write.
    outcome, result, error = await create_provider(token, payload, transport=transport)
    row = await session.get(
        GmailDraftSave, identifier, with_for_update=True, populate_existing=True
    )
    row.state, row.result, row.error_code = outcome, result, error
    await session.commit()
    return view(row)


async def create_provider(token, payload, *, transport=None):
    message = {"raw": payload["mime_base64url"]}
    if payload["preview"]["gmail_thread_id"]:
        message["threadId"] = payload["preview"]["gmail_thread_id"]
    try:
        async with (
            asyncio.timeout(35),
            httpx.AsyncClient(
                transport=transport, timeout=30, follow_redirects=False, trust_env=False
            ) as client,
        ):
            async with client.stream(
                "POST",
                DRAFT_URL,
                headers={"Authorization": f"Bearer {token}"},
                json={"message": message},
            ) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        return "outcome_unknown", None, "response_too_large"
                body = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
                if response.status_code == 200 and isinstance(body, dict):
                    mid = body.get("message", {})
                    if (
                        isinstance(mid, dict)
                        and isinstance(body.get("id"), str)
                        and ID.fullmatch(body["id"])
                        and isinstance(mid.get("id"), str)
                        and ID.fullmatch(mid["id"])
                        and isinstance(mid.get("labelIds"), list)
                        and "DRAFT" in mid["labelIds"]
                        and "SENT" not in mid.get("labelIds", [])
                        and "INBOX" not in mid.get("labelIds", [])
                    ):
                        return "succeeded", {"draft_id": body["id"], "message_id": mid["id"]}, None
                error = body.get("error", {}) if isinstance(body, dict) else {}
                if (
                    response.status_code in (400, 401, 403)
                    and isinstance(error, dict)
                    and error.get("code") == response.status_code
                ):
                    return "failed", None, f"gmail_rejected_{response.status_code}"
    except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError):
        pass
    return "outcome_unknown", None, "gmail_confirmation_unavailable"
