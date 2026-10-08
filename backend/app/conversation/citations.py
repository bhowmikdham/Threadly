"""Verify chat-local user quotes independently of the current requested operation."""

import json

from sqlalchemy import select

from app.auth.crypto import decrypt_token
from app.db.models import ConversationExchange


async def resolve(runtime, citations):
    if not citations:
        return {}
    from app.conversation import store

    versions = {citation.turn_version for citation in citations}
    async with runtime.factory() as session:
        chat = await store.owned(session, runtime.owner, runtime.request.conversation_id)
        if max(versions) > chat.version:
            raise ValueError("Cite only completed turns from this chat")
        rows = (
            await session.scalars(
                select(ConversationExchange).where(
                    ConversationExchange.conversation_id == chat.id,
                    ConversationExchange.version.in_(versions),
                )
            )
        ).all()
        entries = {
            row.version: json.loads(decrypt_token(row.payload_enc))["exchange"] for row in rows
        }
        for entry in store.decode(chat).get("history", []):
            entries.setdefault(entry.get("version"), entry)
    result = {}
    for index, citation in enumerate(citations):
        entry = entries.get(citation.turn_version, {})
        if citation.quote not in entry.get("user", ""):
            raise ValueError("Copy an exact USER quote from a retained turn in this chat")
        result[getattr(citation, "field", str(index))] = {
            "turn_version": citation.turn_version,
            "source": citation.quote,
            "request_id": entry.get("request_id"),
            "recorded_at": entry.get("recorded_at"),
            "timezone": entry.get("timezone"),
        }
    return result
