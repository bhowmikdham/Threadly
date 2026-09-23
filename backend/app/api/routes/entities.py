"""Owned captured-source candidate facts and explicitly selected plan commitments."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.assistant import facts
from app.db.engine import get_session
from app.mail.dependency import gmail_sources

router = APIRouter(dependencies=[Depends(gmail_sources)])
DB = Annotated[AsyncSession, Depends(get_session)]


@router.get("/entities")
async def list_entities(
    user_id: CurrentUser,
    session: DB,
    context_snapshot_id: Annotated[str, Query(min_length=1, max_length=36)],
    type: Annotated[str | None, Query(max_length=40)] = None,
) -> dict:
    return await facts.execute(session, user_id, context_snapshot_id, "lookup_entity", type)


@router.get("/commitments")
async def list_commitments(
    user_id: CurrentUser,
    session: DB,
    context_snapshot_id: Annotated[str, Query(min_length=1, max_length=36)],
) -> dict:
    return await facts.execute(session, user_id, context_snapshot_id, "lookup_commitments")
