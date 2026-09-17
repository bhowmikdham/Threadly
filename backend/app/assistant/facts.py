"""Bounded captured-source facts and explicitly selected plan commitments.

Regex matches are candidates, never proof that a flight/booking/amount is valid.
No global mailbox scan, enrichment URL or model inference is performed.
"""

from sqlalchemy import select

from app.api.errors import ApiError
from app.assistant import reads
from app.assistant.summary import digest
from app.db.models import ArtifactRevision, AssistantTask, ContextSnapshot
from app.extractor.patterns import PATTERNS

RELEASE = "captured-facts-1.0.0"
LIMIT = 50


async def capture(session, owner, context_id):
    context = await session.scalar(
        select(ContextSnapshot).where(
            ContextSnapshot.id == context_id, ContextSnapshot.user_id == owner
        )
    )
    if context is None:
        raise ApiError(404, "context_not_found", "Select an owned source capture.")
    await reads.validate_source(session, owner, context.payload, "search_mail")
    return context


def entities(context, entity_type=None):
    if entity_type is not None and entity_type not in PATTERNS:
        raise ApiError(422, "entity_type_unknown", "Select a supported entity type.")
    items = []
    for message in context.payload["messages"]:
        for kind, pattern in PATTERNS.items():
            if entity_type is not None and kind != entity_type:
                continue
            for match in pattern.finditer(message["body"]):
                quote = match.group()
                items.append(
                    {
                        "type": kind,
                        "value": quote,
                        "quote": quote,
                        "status": "extracted_candidate",
                        "message_id": message["message_id"],
                        "source_hash": digest(message),
                        "offset": match.start(),
                    }
                )
                if len(items) > LIMIT:
                    return answer(context, "lookup_entity", items[:LIMIT], True)
    return answer(context, "lookup_entity", items, False)


async def commitments(session, owner, context):
    rows = (
        await session.scalars(
            select(ArtifactRevision)
            .join(AssistantTask, AssistantTask.final_artifact_id == ArtifactRevision.id)
            .join(ContextSnapshot, ContextSnapshot.id == AssistantTask.context_snapshot_id)
            .where(
                ArtifactRevision.user_id == owner,
                AssistantTask.user_id == owner,
                ContextSnapshot.user_id == owner,
                ContextSnapshot.thread_id == context.thread_id,
                AssistantTask.state == "succeeded",
                ArtifactRevision.payload["kind"].as_string() == "plan",
            )
            .order_by(ArtifactRevision.id)
            .limit(LIMIT + 1)
        )
    ).all()
    items = []
    truncated = len(rows) > LIMIT
    for row in rows[:LIMIT]:
        source = await session.get(ContextSnapshot, row.payload["context_snapshot_id"])
        if source.payload.get("thread_version") != context.payload.get("thread_version"):
            continue
        # Exact source validation also handles UI capture versions and deleted messages.
        try:
            await reads.validate_source(session, owner, source.payload, "search_mail")
        except ApiError:
            continue
        selected = set(row.payload["content"]["accepted_item_ids"])
        for item in row.payload["content"]["items"]:
            if item["id"] in selected:
                items.append(
                    {
                        **item,
                        "plan_artifact_id": row.id,
                        "plan_revision": row.revision,
                        "status": "user_selected",
                        "completed": False,
                    }
                )
    return answer(context, "lookup_commitments", items[:LIMIT], truncated or len(items) > LIMIT)


def answer(context, operation, items, truncated):
    return {
        "schema_version": "1.0",
        "kind": "answer",
        "context_snapshot_id": context.id,
        "coverage": "partial",
        "content": {
            "operation": operation,
            "items": items,
            "scope": "saved_capture"
            if operation == "lookup_entity"
            else "current_selected_plans_for_captured_thread",
            "no_results": not items,
            "truncated": truncated,
            "limit": LIMIT,
        },
        "assumptions": [
            "Only the stated captured scope was inspected. "
            "No result is not proof of absence elsewhere.",
            "Extracted values are unverified candidates; "
            "selected plan items are not completed work.",
        ],
    }


async def execute(session, owner, context_id, operation, entity_type=None):
    context = await capture(session, owner, context_id)
    return (
        entities(context, entity_type)
        if operation == "lookup_entity"
        else await commitments(session, owner, context)
    )
