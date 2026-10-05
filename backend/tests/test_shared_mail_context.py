"""Replay reported context loss through conversation -> source plan -> durable worker.

Synthetic Gmail/model adapters; real isolated PostgreSQL. No live quality claims.
"""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update

from app.api.errors import ApiError
from app.assistant import context_plan, drafting, source_data, summary_quality, tasks, worker
from app.assistant.summary import digest
from app.conversation import mail_context, service, store
from app.conversation.runtime import Runtime
from app.db.models import (
    ArtifactRevision,
    AssistantTask,
    ContextSnapshot,
    Conversation,
    Message,
    User,
)
from app.mail import dependency, live
from app.schemas.assistant import AssistantRequest, DraftOptions
from app.schemas.conversation import PrepareWorkflow, ReadEmail, ReviseDraft, SearchMail
from app.schemas.workflow import WorkflowRequest
from tests.test_conversation import Model, configured, request, tool  # noqa: F401
from tests.test_on_demand_gmail import MID, TID, setup  # noqa: F401
from tests.test_on_demand_gmail import Model as Generator

EARLY = "Please provide employment verification."
SENT = "I provided the requested employment dates yesterday."
LATEST = "Thank you, we received your response."
RELATED = "The employment end date is January 10, 2025."
OTHER_TID = "abc999"


@pytest.fixture()
def mailbox(configured, monkeypatch):  # noqa: F811
    original = live.thread
    state = {"changed": False}

    async def fetch(owner, thread_id):
        source = await original(owner, TID)
        base = source["messages"][0]
        texts = [EARLY, SENT, LATEST] if thread_id == TID else [RELATED]
        source["thread_id"] = thread_id
        source["messages"] = []
        for i, text in enumerate(texts):
            message = deepcopy(base)
            message.update(
                gmail_msg_id=["def454", "def455", MID][i] if thread_id == TID else "def999",
                gmail_thread_id=thread_id,
                body_clean=text
                + (" Changed." if state["changed"] and thread_id == OTHER_TID else ""),
                is_from_user=i == 1,
            )
            source["messages"].append(message)
        source["fingerprint"] = digest(source["messages"])
        return source

    monkeypatch.setattr(live, "thread", fetch)
    return state


async def pinned(factory):
    await source_data.fetch(1, TID)
    async with factory.begin() as session:
        return await source_data.capture(session, 1, TID, message_id=MID)


async def bundle(factory):
    await source_data.fetch(1, TID)
    await source_data.fetch(1, OTHER_TID)
    async with factory.begin() as session:
        return await context_plan.capture(
            session,
            1,
            [
                {"thread_id": TID, "scope": "thread"},
                {"thread_id": OTHER_TID, "scope": "thread"},
            ],
            MID,
        )


def test_thread_default_and_explicit_single_message_contract():
    assert ReadEmail(reference="selected").scope == "thread"
    assert PrepareWorkflow(intent="reply").source_scope == "thread"
    assert ReadEmail(reference="selected", scope="selected_message").scope == "selected_message"
    for values in (["a"] * 2, ["a"] * 5, ["selected"]):
        with pytest.raises(ValidationError):
            PrepareWorkflow(intent="reply", reference="selected", context_references=values)


def test_budget_preserves_boundaries_target_and_short_messages():
    rows = [{"gmail_msg_id": str(i)} for i in range(200)]
    sampled = context_plan.sample(rows, 10, "83")
    ids = [m["gmail_msg_id"] for m in sampled]
    assert len(ids) == 10 and {"0", "199", "83"}.issubset(ids)
    lengths = [50, 100, 100000]
    limits = context_plan.fair_limits(lengths, 24000)
    assert limits == [50, 100, 23850]


async def test_collapsed_messages_reach_summary_worker(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        selected = await pinned(db_sessionmaker)
        turn = request(context_snapshot_id=selected.id).model_copy(
            update={"instruction": "Summarise this thread."}
        )
        result = await service.turn(
            1,
            turn,
            factory=db_sessionmaker,
            model=Model(
                tool("read_email", reference="selected"),
                tool("prepare_workflow", intent="summarise", reference="selected"),
            ),
        )
    generator = Generator()
    assert await worker.run_once(db_sessionmaker, generator)
    prompt = generator.calls[-1]
    assert all(text in prompt for text in (EARLY, SENT, LATEST))
    assert '"total_messages": 3' in prompt
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, result["task_id"])
        assert task.state == "succeeded", task.error_code
        saved = await session.get(ContextSnapshot, task.context_snapshot_id)
        assert saved.payload["storage"] == context_plan.STORAGE
        assert not any(text in json.dumps(saved.payload) for text in (EARLY, SENT, LATEST))
        assert await session.scalar(select(func.count()).select_from(Message)) == 0


async def test_multi_thread_reply_keeps_target_and_context(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        saved = await bundle(db_sessionmaker)
        data = source_data.context_data(saved)
        async with db_sessionmaker() as session:
            user = await session.get(User, 1)
            envelope = await drafting.bind_input(
                session,
                user,
                DraftOptions(
                    to=["sender@example.test"],
                    reply_message_id=MID,
                ),
                saved,
            )
        assert envelope["reply"]["gmail_thread_id"] == TID
        assert envelope["reply"]["gmail_message_id"] == MID
        for mode in ("reply", "compose"):
            prompt = drafting.make_prompt("Prepare a factual response", data, envelope, mode)
            assert all(text in prompt for text in (EARLY, SENT, LATEST, RELATED))
            assert '"thread": 2' in prompt
            if mode == "reply":
                assert prompt.count('"reply_target": true') == 1
        assert RELATED in summary_quality.make_prompt(data, "Summarise both threads")
        with pytest.raises(ApiError):
            async with db_sessionmaker() as session:
                await drafting.bind_input(
                    session,
                    await session.get(User, 1),
                    DraftOptions(
                        to=["sender@example.test"],
                        reply_message_id="def999",
                    ),
                    saved,
                )


async def test_workflow_binds_multiple_read_threads_and_refreshes_all(mailbox, db_sessionmaker):
    turn = request().model_copy(update={"instruction": "Summarise both threads"})
    state = {
        "history": [],
        "result_order": ["mail-1", "mail-2"],
        "refs": {
            "mail-1": {"thread_id": TID, "message_id": MID},
            "mail-2": {"thread_id": OTHER_TID, "message_id": "def999"},
        },
    }
    async with source_data.source_scope():
        runtime = Runtime(1, turn, state, db_sessionmaker)
        await runtime.read("mail-1", "thread")
        with pytest.raises(ValueError, match="supporting"):
            await runtime.workflow(
                PrepareWorkflow(
                    intent="summarise",
                    reference="mail-1",
                    context_references=["mail-2"],
                )
            )
        await runtime.read("mail-2", "thread")
        result = await runtime.workflow(
            PrepareWorkflow(
                intent="summarise",
                reference="mail-1",
                context_references=["mail-2"],
            )
        )
    generator = Generator()
    await worker.run_once(db_sessionmaker, generator)
    assert RELATED in generator.calls[-1] and SENT in generator.calls[-1]
    async with db_sessionmaker() as session:
        assert (await session.get(AssistantTask, result["task_id"])).state == "succeeded"


async def test_secondary_change_blocks_materialization_and_freshness(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        saved = await bundle(db_sessionmaker)
        mailbox["changed"] = True
        with pytest.raises(ApiError) as exc:
            await source_data.recheck(1, saved.payload)
        assert exc.value.code == "source_changed"
    async with source_data.source_scope():
        with pytest.raises(ApiError) as exc:
            await source_data.prefetch(1, [saved.payload])
        assert exc.value.code == "source_changed"


async def test_cross_owner_plan_rejected_before_fetch(mailbox, db_sessionmaker, monkeypatch):
    async with source_data.source_scope():
        saved = await bundle(db_sessionmaker)

    async def forbidden(*args):
        raise AssertionError("Unauthorized source must not be fetched")

    monkeypatch.setattr(live, "thread", forbidden)
    async with source_data.source_scope():
        with pytest.raises(ApiError) as exc:
            await source_data.prefetch(2, [saved.payload])
        assert exc.value.code == "context_not_found"


async def test_unchanged_recapture_preserves_primary_version(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        saved = await bundle(db_sessionmaker)
        async with db_sessionmaker.begin() as session:
            # UUID tie-breaking must not accidentally hide the newest-plan case.
            await session.execute(
                update(ContextSnapshot)
                .where(ContextSnapshot.id == saved.id)
                .values(created_at=datetime.now(UTC) + timedelta(seconds=1))
            )
        async with db_sessionmaker.begin() as session:
            recaptured = await source_data.capture(session, 1, TID)
            assert recaptured.payload["thread_version"] == saved.payload["thread_version"]
            envelope = await drafting.bind_input(
                session,
                await session.get(User, 1),
                DraftOptions(
                    to=["sender@example.test"],
                    reply_message_id=MID,
                ),
                saved,
            )
            assert envelope["reply"]["thread_version"] == recaptured.payload["thread_version"]


async def test_retained_refs_survive_search_reset_and_rehydrate_next_turn(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        selected = await pinned(db_sessionmaker)
        turn = request(context_snapshot_id=selected.id).model_copy(
            update={"instruction": "Read this thread"}
        )
        first = await service.turn(
            1,
            turn,
            factory=db_sessionmaker,
            model=Model(
                tool("read_email", reference="selected"),
                tool(
                    "respond",
                    kind="message",
                    text="The response was acknowledged.",
                    evidence=[{"reference": "selected", "quote": LATEST}],
                ),
            ),
        )
    assert first["context_references"] == ["context-1"]
    async with db_sessionmaker() as session:
        row = await session.get(Conversation, turn.conversation_id)
        state = store.decode(row)
    mail_context.reset_search(state)
    assert "context-1" in state["refs"]
    assert not any(text in json.dumps(state["refs"]) for text in (EARLY, SENT, LATEST))
    next_turn = request().model_copy(update={"instruction": "Draft a reply using that thread"})
    async with source_data.source_scope():
        runtime = Runtime(1, next_turn, state, db_sessionmaker)
        observed = await runtime.read("context-1", "thread")
    assert [m["body"] for m in observed["messages"]] == [EARLY, SENT, LATEST]
    assert state["history"][-1]["context_references"] == ["context-1"]


def test_retained_memory_is_bounded_and_handles_never_reused():
    state = {"refs": {}}
    keys = []
    for i in range(12):
        state["refs"]["selected"] = {"thread_id": str(i), "message_id": str(i)}
        keys.append(mail_context.retain(state, "selected", "thread"))
    assert len(state["context_order"]) == mail_context.LIMIT
    assert len(set(keys)) == 12
    assert keys[0] not in state["refs"]


async def test_new_policy_does_not_reinterpret_historical_reference(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        old = await pinned(db_sessionmaker)
        before = source_data.context_data(old)
        await bundle(db_sessionmaker)
        assert source_data.context_data(old) == before
        assert before["messages"][0]["body"] == LATEST
        assert "context_plan" not in before


@pytest.mark.parametrize("full_thread", [False, True])
async def test_two_cards_from_same_thread_coalesce_without_scope_loss(
    mailbox,
    db_sessionmaker,
    full_thread,
):
    async with source_data.source_scope():
        await source_data.fetch(1, TID)
        async with db_sessionmaker.begin() as session:
            saved = await context_plan.capture(
                session,
                1,
                [
                    {"thread_id": TID, "scope": "selected_message", "message_ids": [MID]},
                    {
                        "thread_id": TID,
                        "scope": "thread" if full_thread else "selected_message",
                        "message_ids": None if full_thread else ["def454"],
                    },
                ],
                MID,
            )
        data = source_data.context_data(saved)
    assert len(saved.payload["sources"]) == 1
    assert {m["message_id"] for m in data["messages"]} == (
        {"def454", "def455", MID} if full_thread else {MID, "def454"}
    )


async def submit_reply(factory, saved):
    async with factory.begin() as session:
        return await tasks.submit(
            session,
            1,
            AssistantRequest(
                schema_version="1.0",
                request_id="context-reply",
                instruction="Draft a reply",
                intent_hint="reply",
                context_snapshot_id=saved.id,
                continuation=None,
                draft_options=DraftOptions(to=["sender@example.test"], reply_message_id=MID),
            ),
        )


async def test_reply_worker_edit_and_preview_prefetch_every_source(mailbox, db_sessionmaker):
    from app.actions import email_preview

    async with source_data.source_scope():
        saved = await bundle(db_sessionmaker)
        task = await submit_reply(db_sessionmaker, saved)
    generator = Generator()
    await worker.run_once(db_sessionmaker, generator)
    assert all(text in generator.calls[-1] for text in (EARLY, SENT, LATEST, RELATED))
    async with db_sessionmaker() as session:
        task = await session.get(AssistantTask, task.id)
        assert task.state == "succeeded", task.error_code
        artifact = await session.scalar(
            select(ArtifactRevision).where(ArtifactRevision.task_id == task.id)
        )
        assert artifact.payload["content"]["thread_ref"] == TID
    async with source_data.source_scope():
        refs = await dependency.references(1, [artifact.id], factory=db_sessionmaker)
        await source_data.prefetch(1, refs)
        assert source_data.source_for(1, OTHER_TID)["thread_id"] == OTHER_TID
        async with db_sessionmaker() as session:
            await email_preview.sources(session, task, artifact)
    # The conversation revision path is not an artifact HTTP endpoint; it must
    # independently prefetch every source, not just the reply target thread.
    async with source_data.source_scope():
        turn = request().model_copy(update={"instruction": "Make the draft shorter"})
        runtime = Runtime(1, turn, {"history": [], "refs": {}, "result_order": []}, db_sessionmaker)
        runtime.artifact = artifact
        result = await runtime.revise(ReviseDraft(subject="Re: Agenda", body="Thank you."))
        assert result["kind"] == "task"
        assert source_data.source_for(1, OTHER_TID)["thread_id"] == OTHER_TID
    mailbox["changed"] = True
    async with source_data.source_scope():
        with pytest.raises(ApiError, match="changed"):
            await source_data.prefetch(1, refs)


@pytest.mark.parametrize("intent", ["summarise", "reply", "workflow_summary"])
async def test_secondary_source_changes_during_generation_block_publication(
    mailbox,
    db_sessionmaker,
    intent,
):
    async with source_data.source_scope():
        saved = await bundle(db_sessionmaker)
        if intent == "reply":
            task = await submit_reply(db_sessionmaker, saved)
        elif intent == "workflow_summary":
            workflow = WorkflowRequest(
                schema_version="1.0",
                request_id="context-workflow-summary",
                instruction="Summarise both threads",
                context_snapshot_id=saved.id,
                operations=["summary"],
            )
            async with db_sessionmaker.begin() as session:
                task = await tasks.submit(session, 1, workflow.as_request(), workflow=workflow)
        else:
            async with db_sessionmaker.begin() as session:
                task = await tasks.submit(
                    session,
                    1,
                    AssistantRequest(
                        schema_version="1.0",
                        request_id="context-summary",
                        instruction="Summarise this thread",
                        intent_hint="summarise",
                        context_snapshot_id=saved.id,
                        continuation=None,
                    ),
                )

    class ChangedDuringGeneration(Generator):
        async def generate(self, prompt, **kwargs):
            result = await super().generate(prompt, **kwargs)
            mailbox["changed"] = True
            return result

    model = ChangedDuringGeneration()
    await worker.run_once(db_sessionmaker, model)
    assert RELATED in model.calls[-1]
    async with db_sessionmaker() as session:
        current = await session.get(AssistantTask, task.id)
        assert current.state == "failed" and current.error_code == "source_changed"
        assert await session.scalar(select(func.count()).select_from(ArtifactRevision)) == 0


async def test_retained_ui_scope_does_not_expand_after_pin_changes(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        selected = await pinned(db_sessionmaker)
        state = {
            "history": [],
            "result_order": [],
            "refs": {
                "selected": {"thread_id": TID, "message_id": MID, "context_id": selected.id},
            },
        }
        runtime = Runtime(1, request(), state, db_sessionmaker)
        first = await runtime.read("selected", "visible_thread")
        state["refs"].pop("selected")
        later = await runtime.read(first["remembered_reference"], "visible_thread")
        assert [m["body"] for m in later["messages"]] == [LATEST]
        context_id, _ = await runtime.capture(first["remembered_reference"], scope="visible_thread")
        assert context_id == selected.id


async def test_new_search_preserves_read_primary_plan(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        selected = await pinned(db_sessionmaker)
        turn = request().model_copy(update={"instruction": "Find agenda and summarise this thread"})
        state = {
            "history": [],
            "result_order": [],
            "refs": {
                "selected": {"thread_id": TID, "message_id": MID, "context_id": selected.id},
            },
        }
        runtime = Runtime(1, turn, state, db_sessionmaker)
        await runtime.read("selected", "thread")
        await runtime.search(SearchMail(query="agenda"))
        context_id, mid = await runtime.capture("selected", scope="thread")
        async with db_sessionmaker() as session:
            data = source_data.context_data(await session.get(ContextSnapshot, context_id))
        assert mid == MID
        assert [m["body"] for m in data["messages"]] == [EARLY, SENT, LATEST]


async def test_supporting_scope_tracks_latest_read_after_batch_inspection(mailbox, db_sessionmaker):
    async with source_data.source_scope():
        state = {
            "history": [],
            "result_order": ["mail-1", "mail-2"],
            "refs": {
                "mail-1": {"thread_id": OTHER_TID, "message_id": "def999"},
                "mail-2": {"thread_id": TID, "message_id": MID},
            },
        }
        runtime = Runtime(1, request(), state, db_sessionmaker)
        await runtime.read_search_results(["mail-1", "mail-2"])
        await runtime.read("mail-2", "thread")
        context_id, _ = await runtime.capture(
            "mail-1", scope="selected_message", supporting=["mail-2"]
        )
        async with db_sessionmaker() as session:
            data = source_data.context_data(await session.get(ContextSnapshot, context_id))
        assert SENT in [m["body"] for m in data["messages"]]
        # A later deliberate narrowing must not silently retain the wider scope.
        await runtime.read("mail-2", "selected_message")
        context_id, _ = await runtime.capture(
            "mail-1", scope="selected_message", supporting=["mail-2"]
        )
        async with db_sessionmaker() as session:
            data = source_data.context_data(await session.get(ContextSnapshot, context_id))
        assert [m["body"] for m in data["messages"]] == [RELATED, LATEST]
