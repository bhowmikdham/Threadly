"""Serialize edits/reviews through task locks; never perform external writes.

Revision payloads/envelopes are immutable. A review acknowledges one exact hash;
its effective state is derived from the current revision and local source state.
"""

import copy
import re
from uuid import uuid4

from sqlalchemy import select

from app.api.errors import ApiError
from app.assistant import tasks
from app.assistant.summary import digest
from app.db.models import ArtifactRevision, DraftReview, Message, Thread, User
from app.schemas.draft_review import EditDraftRequest, ReviewDraftRequest

POLICY = "draft-review-1.0.0"


async def latest(session, task, stream_key=None):
    if stream_key is None:
        return await session.scalar(
            select(ArtifactRevision).where(
                ArtifactRevision.id == task.final_artifact_id,
                ArtifactRevision.task_id == task.id,
                ArtifactRevision.user_id == task.user_id,
            )
        )
    return await session.scalar(
        select(ArtifactRevision)
        .where(
            ArtifactRevision.task_id == task.id,
            ArtifactRevision.user_id == task.user_id,
            ArtifactRevision.stream_key == stream_key,
        )
        .order_by(ArtifactRevision.revision.desc())
        .limit(1)
    )


async def owned_artifact(session, user_id, artifact_id):
    artifact = await session.scalar(
        select(ArtifactRevision).where(
            ArtifactRevision.id == artifact_id, ArtifactRevision.user_id == user_id
        )
    )
    if artifact is None:
        raise ApiError(404, "not_found", "Unknown artifact.")
    return artifact


def require_draft(artifact):
    if artifact is None or artifact.payload.get("kind") != "draft":
        raise ApiError(409, "draft_required", "This task has no editable draft.")
    if not artifact.draft_envelope:
        raise ApiError(409, "draft_envelope_unavailable", "The saved draft has no usable envelope.")


def payload_hash(artifact):
    return digest(
        {
            "policy": POLICY,
            "artifact_id": artifact.id,
            "revision": artifact.revision,
            "artifact": artifact.payload,
            "envelope": artifact.draft_envelope,
        }
    )


async def blockers(session, artifact):
    envelope = artifact.draft_envelope
    if not envelope:
        return ["draft_envelope_unavailable"]
    content = artifact.payload["content"]
    result = []
    if not envelope.get("to"):
        result.append("recipients_required")
    if content["unresolved_fields"] or re.search(
        r"\[[^\]\n]{1,200}\]|\{\{", content["body"] + "\n" + content["subject"]
    ):
        result.append("unresolved_fields")
    user = await session.get(User, artifact.user_id)
    if user is None or user.email != envelope.get("from_address"):
        result.append("sender_changed")
    reply = envelope.get("reply")
    if reply:
        current = (
            await session.execute(
                select(Thread.version)
                .join(Message, Message.thread_id == Thread.id)
                .where(
                    Thread.user_id == artifact.user_id,
                    Message.user_id == artifact.user_id,
                    Thread.gmail_thread_id == reply["gmail_thread_id"],
                    Message.gmail_msg_id == reply["gmail_message_id"],
                )
            )
        ).scalar_one_or_none()
        if current != reply["thread_version"]:
            result.append("reply_context_changed")
        if reply.get("rfc_message_id") is None:
            result.append("reply_headers_unavailable")
    return result


async def artifact_view(session, task, artifact, current=None):
    current = current or await latest(session, task, artifact.stream_key)
    is_draft = artifact.payload.get("kind") == "draft"
    review = await session.get(DraftReview, artifact.id) if is_draft else None
    blocked = await blockers(session, artifact) if is_draft else []
    is_latest = current.id == artifact.id
    hashed = payload_hash(artifact) if is_draft else None
    valid_review = review and is_latest and not blocked and review.payload_hash == hashed
    return {
        "artifact_id": artifact.id,
        "task_id": artifact.task_id,
        "revision": artifact.revision,
        "stream_key": artifact.stream_key,
        "is_final_result": task.final_artifact_id == artifact.id,
        "artifact": artifact.payload,
        "provenance": artifact.provenance,
        "draft_envelope": artifact.draft_envelope if is_draft else None,
        "sending_available": False,
        "is_latest": is_latest,
        "latest_artifact_id": current.id,
        "latest_revision": current.revision,
        "review": {
            "policy": POLICY,
            "payload_hash": hashed,
            "state": "reviewed" if valid_review else "stale" if review else "unreviewed",
            "reviewed_at": review.created_at.isoformat() if review else None,
            "blockers": blocked + ([] if is_latest else ["revision_superseded"]),
            "authorization": "none",
        }
        if is_draft
        else None,
    }


async def edit(session, user_id, task_id, request: EditDraftRequest):
    task = await tasks.owned_task(session, user_id, task_id, lock=True)
    hashed = digest(request.model_dump())
    previous = await session.scalar(
        select(ArtifactRevision).where(
            ArtifactRevision.task_id == task.id,
            ArtifactRevision.user_id == user_id,
            ArtifactRevision.edit_request_id == request.request_id,
        )
    )
    if previous:
        if previous.edit_request_hash != hashed:
            raise ApiError(
                409, "idempotency_conflict", "Edit request ID was used for different input."
            )
        return task, previous
    current = await latest(session, task)
    require_draft(current)
    if task.state != "succeeded" or current.revision != request.expected_revision:
        raise ApiError(409, "revision_conflict", "Draft changed; reload before saving edits.")
    if not request.recipients.to:
        raise ApiError(422, "recipients_required", "Select at least one To recipient.")
    envelope = copy.deepcopy(current.draft_envelope)
    if envelope.get("reply") and request.subject != envelope["reply"]["subject"]:
        raise ApiError(409, "reply_subject_fixed", "Start a new email to change the reply subject.")
    for field in ("to", "cc", "bcc"):
        envelope[field] = getattr(request.recipients, field)
    artifact_id = str(uuid4())
    payload = copy.deepcopy(current.payload)
    content = payload["content"]
    content.update(
        subject=request.subject,
        body=request.body,
        unresolved_fields=list(dict.fromkeys(request.unresolved_fields)),
    )
    for field in ("to", "cc", "bcc"):
        content[field + "_refs"] = [f"{field}-{n}" for n in range(1, len(envelope[field]) + 1)]
    # Original excerpts remain background evidence, not validation of new user-authored claims.
    payload["evidence"] = [
        {
            "ref_id": "user-edit",
            "source_kind": "user_input",
            "source_id": artifact_id,
            "source_version": hashed,
            "quote": None,
        }
    ]
    content["fact_ref_ids"] = ["user-edit"]
    payload["assumptions"] = [
        "User-edited text; facts have not been revalidated by a model or live source.",
        "Stored in Threadly only; review does not authorize sending or editor insertion.",
        "No live availability check or attachment support.",
    ]
    artifact = ArtifactRevision(
        id=artifact_id,
        task_id=task.id,
        user_id=user_id,
        revision=current.revision + 1,
        stream_key=current.stream_key,
        payload=payload,
        draft_envelope=envelope,
        edit_request_id=request.request_id,
        edit_request_hash=hashed,
        provenance={"source": "user_edit", "policy": POLICY, "parent_artifact_id": current.id},
    )
    session.add(artifact)
    task.final_artifact_id = artifact.id
    task.version += 1
    tasks.add_event(
        session,
        task,
        "draft.revised",
        {
            "artifact_id": artifact.id,
            "revision": artifact.revision,
            "supersedes_artifact_id": current.id,
            "review_state": "unreviewed",
        },
    )
    await session.flush()
    return task, artifact


async def review(session, user_id, artifact_id, request: ReviewDraftRequest):
    artifact = await owned_artifact(session, user_id, artifact_id)
    task = await tasks.owned_task(session, user_id, artifact.task_id, lock=True)
    require_draft(artifact)
    current = await latest(session, task)
    if current.id != artifact.id or artifact.revision != request.expected_revision:
        raise ApiError(409, "revision_conflict", "Review the latest draft revision.")
    hashed = payload_hash(artifact)
    if request.payload_hash != hashed:
        raise ApiError(409, "review_payload_changed", "Reload the exact draft before reviewing.")
    if await blockers(session, artifact):
        raise ApiError(409, "draft_review_blocked", "Resolve the blockers shown on the draft.")
    existing = await session.get(DraftReview, artifact.id)
    if existing is None:
        session.add(DraftReview(artifact_id=artifact.id, user_id=user_id, payload_hash=hashed))
        task.version += 1
        tasks.add_event(
            session,
            task,
            "draft.reviewed",
            {
                "artifact_id": artifact.id,
                "revision": artifact.revision,
                "authorization": "none",
            },
        )
        await session.flush()
    return task, artifact
