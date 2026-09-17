"""Conservative explicit cleanup. Dry-run by default; no action/task deletion.

Only orphaned captures, expired unconsumed proposals/OAuth state and staging from
terminal sync jobs are eligible. Retain task/artifact/action recovery and audit
records until a separately approved account-deletion policy covers them.
"""

import asyncio
import json
from datetime import timedelta

from sqlalchemy import delete, func, select

from app.db.engine import get_session_factory
from app.db.models import (
    ArtifactRevision,
    AssistantTask,
    CommandPlan,
    ContextSnapshot,
    GoogleOAuthSession,
    MailSyncJob,
    MailSyncStage,
    SchedulingProposal,
    TaskInput,
)


async def cleanup(factory, *, apply=False, days=7, limit=100):
    if not 1 <= days <= 365 or not 1 <= limit <= 1000:
        raise ValueError("Invalid retention bounds")
    async with factory.begin() as session:
        cutoff = await session.scalar(select(func.clock_timestamp())) - timedelta(days=days)
        result = {
            "mode": "apply" if apply else "dry_run",
            "days": days,
            "limit_per_table": limit,
            "counts": {},
        }
        # Unconsumed proposals have no task pointer or action authority. Keep consumed
        # ones for provenance even when old. Expired planning reservations are safe too.
        for model in (CommandPlan, SchedulingProposal):
            ids = (
                await session.scalars(
                    select(model.id)
                    .where(model.task_id.is_(None), model.expires_at < cutoff)
                    .order_by(model.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            result["counts"][model.__tablename__] = len(ids)
            if apply and ids:
                await session.execute(delete(model).where(model.id.in_(ids)))
        states = (
            await session.scalars(
                select(GoogleOAuthSession.state_hash)
                .where(GoogleOAuthSession.expires_at < cutoff)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        result["counts"]["google_oauth_sessions"] = len(states)
        if apply and states:
            await session.execute(
                delete(GoogleOAuthSession).where(GoogleOAuthSession.state_hash.in_(states))
            )
        # NOT IN would mishandle nullable context pointers. Correlated NOT EXISTS is deliberate.
        orphan = select(ContextSnapshot.id).where(ContextSnapshot.created_at < cutoff)
        for model in (AssistantTask, TaskInput, CommandPlan, SchedulingProposal):
            orphan = orphan.where(
                ~select(model.id).where(model.context_snapshot_id == ContextSnapshot.id).exists()
            )
        orphan = orphan.where(
            ~select(AssistantTask.id)
            .where(AssistantTask.effective_context_snapshot_id == ContextSnapshot.id)
            .exists()
        )
        orphan = orphan.where(
            ~select(ArtifactRevision.id)
            .where(
                ArtifactRevision.payload["context_snapshot_id"].as_string() == ContextSnapshot.id
            )
            .exists()
        )
        ids = (
            await session.scalars(
                orphan.order_by(ContextSnapshot.id).limit(limit).with_for_update(skip_locked=True)
            )
        ).all()
        result["counts"]["orphan_context_snapshots"] = len(ids)
        if apply and ids:
            await session.execute(delete(ContextSnapshot).where(ContextSnapshot.id.in_(ids)))
        jobs = (
            await session.scalars(
                select(MailSyncJob.id)
                .where(
                    MailSyncJob.state.in_(["succeeded", "failed"]), MailSyncJob.created_at < cutoff
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        result["counts"]["terminal_sync_staging_jobs"] = len(jobs)
        if apply and jobs:
            await session.execute(delete(MailSyncStage).where(MailSyncStage.job_id.in_(jobs)))
        if not apply:
            await session.rollback()
        return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(cleanup(get_session_factory(), apply=args.apply, days=args.days))))
