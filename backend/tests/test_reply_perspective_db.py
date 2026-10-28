"""Disposable-PostgreSQL replay; Gmail/model are synthetic and provider writes forbidden."""

import json

import pytest
from sqlalchemy import func, select

from app.assistant import context_plan, source_data, tasks, worker
from app.db.models import ActionApproval, ArtifactRevision, AssistantAction, GmailDraftSave
from app.model_client.client import GenResult
from app.schemas.assistant import AssistantRequest, DraftOptions
from tests.test_conversation import configured  # noqa: F401
from tests.test_intent_router import proposal
from tests.test_on_demand_gmail import MID, TID, setup  # noqa: F401
from tests.test_shared_mail_context import mailbox  # noqa: F401


@pytest.mark.parametrize("scope", ["thread", "selected_message"])
async def test_reply_perspective_survives_source_capture_and_idempotent_worker(
    scope, mailbox, db_sessionmaker  # noqa: F811
):
    async with source_data.source_scope():
        await source_data.fetch(1, TID)
        async with db_sessionmaker.begin() as session:
            context = (
                await context_plan.capture(session, 1, [{"thread_id": TID, "scope": "thread"}], MID)
                if scope == "thread"
                else await source_data.capture(session, 1, TID, message_id=MID)
            )
            request = AssistantRequest(
                schema_version="1.0",
                request_id="reply-perspective-retry",
                instruction="Draft a reply thanking the sender for acknowledging my response.",
                intent_hint="reply",
                context_snapshot_id=context.id,
                draft_options=DraftOptions(to=["sender@example.test"], reply_message_id=MID),
            )
            task = await tasks.submit(session, 1, request)
            assert (await tasks.submit(session, 1, request)).id == task.id

    class Generator:
        def __init__(self):
            self.drafts = []

        async def generate(self, prompt, **kwargs):
            if prompt.startswith("Write an email draft"):
                payload = json.loads(prompt.split("DRAFT_REQUEST_JSON:\n")[1])
                assert payload["draft_author"] == "mailbox_owner"
                assert payload["messages"][-1]["author_role"] == "correspondent"
                assert payload["messages"][-1]["reply_target"] is True
                if scope == "thread":
                    assert [m["author_role"] for m in payload["messages"]] == [
                        "correspondent",
                        "mailbox_owner",
                        "correspondent",
                    ]
                self.drafts.append(payload)
                output = {
                    "body": "Thank you for confirming receipt of my response.",
                    "unresolved_fields": [],
                    "sources": [len(payload["messages"])],
                }
            else:
                output = proposal(intent="reply", output_kind="draft", operations=["draft_reply"])
            return json.dumps(output), GenResult("fake", "reply-perspective-contract")

    model = Generator()
    assert await worker.run_once(db_sessionmaker, model)
    assert not await worker.run_once(db_sessionmaker, model)
    assert len(model.drafts) == 1
    async with db_sessionmaker() as session:
        rows = (
            await session.scalars(
                select(ArtifactRevision).where(ArtifactRevision.task_id == task.id)
            )
        ).all()
        assert len(rows) == 1
        artifact = rows[0]
        assert artifact.draft_envelope["from_address"] == "owner1@example.test"
        assert artifact.draft_envelope["to"] == ["sender@example.test"]
        assert artifact.draft_envelope["reply"]["gmail_message_id"] == MID
        assert artifact.payload["content"]["mode"] == "reply"
        for table in (AssistantAction, ActionApproval, GmailDraftSave):
            assert await session.scalar(select(func.count()).select_from(table)) == 0
