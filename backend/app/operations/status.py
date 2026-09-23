"""Owner-scoped operational metadata. No prompts, mail bodies, addresses or tokens."""

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import ActionJob, AssistantAction, AssistantJob, AssistantTask, MailSyncJob


async def counts(session, model, owner):
    return dict(
        (
            await session.execute(
                select(model.state, func.count())
                .where(model.user_id == owner)
                .group_by(model.state)
            )
        ).all()
    )


async def snapshot(session, owner):
    now = await session.scalar(select(func.clock_timestamp()))
    oldest = await session.scalar(
        select(func.min(AssistantTask.created_at)).where(
            AssistantTask.user_id == owner, AssistantTask.state == "queued"
        )
    )
    settings = get_settings()
    return {
        "schema_version": "1.0",
        "tasks": await counts(session, AssistantTask, owner),
        "generation_jobs": await counts(session, AssistantJob, owner),
        "actions": await counts(session, AssistantAction, owner),
        "action_jobs": await counts(session, ActionJob, owner),
        "sync_jobs": await counts(session, MailSyncJob, owner),
        "oldest_queued_seconds": max(0, int((now - oldest).total_seconds())) if oldest else None,
        "controls": {
            "disabled_intents": sorted(settings.assistant_disabled_intents_values),
            "email_writes": settings.email_writes_enabled
            and str(owner) in settings.write_pilot_user_ids_values,
            "calendar_writes": settings.calendar_writes_enabled
            and str(owner) in settings.write_pilot_user_ids_values,
            "email_reconciliation": settings.email_reconciliation_enabled,
            "calendar_reconciliation": settings.calendar_reconciliation_enabled,
            "background_sync": settings.gmail_source_mode != "on_demand"
            and settings.mailbox_background_sync_enabled,
            "gmail_source_mode": settings.gmail_source_mode,
        },
        "readiness": "metadata_only_not_live_provider_verification",
    }


def blocked_intents(claim):
    disabled = get_settings().assistant_disabled_intents_values
    if not disabled:
        return set()
    if claim.workflow_input:
        mapping = {
            "summary": "summarise",
            "schedule": "plan_schedule",
            "plan": "plan_schedule",
            "draft_reply": "reply",
            "draft_new": "compose",
            "lookup_entity": "other",
            "lookup_commitments": "other",
        }
        requested = {mapping[op] for op in claim.workflow_input["request"]["operations"]}
    elif claim.scheduling_input:
        requested = {"plan_schedule"}
    elif claim.read_input:
        requested = {"other"}
    elif claim.compound_input:
        template = claim.compound_input.get("template", "")
        requested = {
            "other" if template.startswith("lookup") else "summarise",
            "reply" if template.endswith("reply") else "compose",
        }
    else:
        requested = {claim.intent_hint} if claim.intent_hint else set()
    return requested & disabled
