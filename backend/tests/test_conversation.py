import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.assistant import source_data
from app.config import get_settings
from app.conversation import engine, service, store
from app.conversation.runtime import (
    _is_contextual_followup,
    authorize_workflow,
    validate_clarification,
    validate_workflow_bindings,
)
from app.db.models import AssistantTask, CommandPlan, Conversation, Message, TaskEvent, User
from app.model_client.conversation import ConversationProviderError
from app.model_client.providers import ProviderError
from app.schemas.continuation import ClarificationAnswer
from app.schemas.conversation import ConversationTurn, PrepareWorkflow
from tests.test_on_demand_gmail import MID, TEXT, TID, setup  # noqa: F401


def tool(name, **values):
    return {
        "role": "assistant",
        "content": [{"toolUse": {"toolUseId": str(uuid4()), "name": name, "input": values}}],
    }


class Model:
    def __init__(self, *decisions):
        self.decisions, self.contexts = list(decisions), []

    async def decide(self, system, messages, tools):
        self.contexts.append(json.loads(messages[0]["content"][0]["text"]))
        if not self.decisions:
            raise ProviderError("Fake exhausted")
        return self.decisions.pop(0)


class Runtime:
    def __init__(self):
        self.evidence, self.calls = {}, []

    async def call(self, name, arguments):
        self.calls.append(name)
        if name == "read_email":
            self.evidence[arguments.reference] = "Automated receipt. No reply required."
            return {"body": self.evidence[arguments.reference]}
        return {"kind": "task", "text": "Draft is being prepared"}


async def test_semantic_decision_has_history_and_no_tool_on_social():
    model = Model(tool("respond", kind="message", text="I'm here and ready to help!"))
    runtime = Runtime()
    context = {
        "user_turn": "how are yo u",
        "recent_dialogue": [{"user": "hey", "assistant": "Hey!"}],
    }
    result = await engine.run(context, runtime, model)
    assert result["kind"] == "message" and runtime.calls == []
    assert model.contexts[0] == context


async def test_read_before_advice_and_quote_validation():
    model = Model(
        tool("read_email", reference="selected"),
        tool(
            "respond",
            kind="recommendation",
            text="This doesn't need a reply.",
            evidence=[{"reference": "selected", "quote": "No reply required."}],
        ),
    )
    result = await engine.run({}, Runtime(), model)
    assert result["kind"] == "recommendation"
    assert [t["tool"] for t in result["trace"]] == ["read_email", "respond"]


async def test_invented_quote_rejected_and_recoverable():
    model = Model(
        tool("read_email", reference="selected"),
        tool(
            "respond",
            kind="recommendation",
            text="Paid",
            evidence=[{"reference": "selected", "quote": "paid in full"}],
        ),
        tool(
            "respond",
            kind="recommendation",
            text="No reply needed.",
            evidence=[{"reference": "selected", "quote": "No reply required."}],
        ),
    )
    result = await engine.run({}, Runtime(), model)
    assert result["trace"][1]["status"] == "invalid"


@pytest.mark.parametrize("name", ["send_email", "create_event", "approve", "delete_mail"])
async def test_external_write_tools_are_not_callable(name):
    runtime = Runtime()
    model = Model(
        tool(name, approval=True),
        tool("respond", kind="message", text="Please review the exact content first."),
    )
    await engine.run({}, runtime, model)
    assert runtime.calls == []


async def test_repeated_call_not_executed_twice():
    runtime = Runtime()
    model = Model(
        tool("read_email", reference="selected"),
        tool("read_email", reference="selected"),
        tool("respond", kind="clarification", text="What would you like to check?"),
    )
    result = await engine.run({}, runtime, model)
    assert runtime.calls == ["read_email"]
    assert result["trace"][1]["status"] == "invalid"


async def test_budget_exhaustion_is_explicit():
    with pytest.raises(ApiError) as exc:
        await engine.run({}, Runtime(), Model(*(tool("missing" + str(i)) for i in range(8))))
    assert exc.value.code == "conversation_tool_limit"


async def test_provider_code_is_not_exposed_in_public_error():
    class Denied:
        async def decide(self, system, messages, tools):
            raise ConversationProviderError("AccessDeniedException")

    with pytest.raises(ApiError) as exc:
        await engine.run({}, Runtime(), Denied())
    assert exc.value.code == "conversation_provider_unavailable"
    assert "AccessDenied" not in exc.value.code + exc.value.message


@pytest.fixture()
def configured(setup, monkeypatch, db_sessionmaker):  # noqa: F811
    monkeypatch.setenv("CONVERSATION_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(service, "get_session_factory", lambda: db_sessionmaker)
    yield setup
    get_settings.cache_clear()


def request(**values):
    return ConversationTurn(
        conversation_id=str(uuid4()),
        request_id=str(uuid4()),
        expected_version=0,
        instruction="hey",
        **values,
    )


def test_turn_hash_distinguishes_omitted_source_from_explicit_clear():
    omitted = request()
    cleared = omitted.model_copy(update={"context_snapshot_id": None})
    assert "context_snapshot_id" not in omitted.model_fields_set
    assert "context_snapshot_id" in cleared.model_fields_set
    assert store.request_hash(omitted) != store.request_hash(cleared)


def test_state_compaction_preserves_current_receipt_and_pending_recovery(configured):
    exact = {"kind": "message", "text": "z" * 4000, "version": 12}
    state = {
        "history": [
            {
                "user": "u" * 4000,
                "assistant": "a" * 4000,
                "kind": "message",
                "request_id": str(uuid4()),
            }
            for _ in range(12)
        ],
        "receipts": [
            {"request_id": f"old-{i}", "hash": "h" * 64, "response": exact} for i in range(12)
        ],
        "pending_result": exact.copy(),
        "refs": {},
        "result_order": [],
    }
    current = state["receipts"][-1]["request_id"]
    store.compact(state, preserve_receipt_id=current)
    assert next(r for r in state["receipts"] if r["request_id"] == current)["response"] == exact
    assert state["pending_result"] == exact
    assert len(store.encode(state)) <= store.STATE_ENCRYPTED_LIMIT


def test_clarification_values_are_fenced_to_latest_turn_and_context():
    question = {
        "fields": [
            "timezone",
            "duration_minutes",
            "date_phrase",
            "time_phrase",
            "am_or_pm",
        ]
    }
    answer = ClarificationAnswer(
        timezone="Australia/Melbourne",
        duration_minutes=30,
        date_phrase="tomorrow",
        time_phrase="4 pm",
        am_or_pm="PM",
    )
    validate_clarification(
        answer,
        "Tomorrow at 4 pm for half an hour",
        question,
        "Australia/Melbourne",
    )
    for invented in (
        ClarificationAnswer(duration_minutes=45),
        ClarificationAnswer(date_phrase="Friday"),
        ClarificationAnswer(time_phrase="5 pm"),
        ClarificationAnswer(am_or_pm="AM"),
        ClarificationAnswer(timezone="Europe/London"),
    ):
        with pytest.raises(ValueError):
            validate_clarification(
                invented,
                "Tomorrow at 4 for half an hour",
                question,
                "Australia/Melbourne",
            )


def test_bare_time_does_not_authorize_invented_ampm():
    with pytest.raises(ValueError):
        validate_clarification(
            ClarificationAnswer(time_phrase="4", am_or_pm="PM"),
            "4",
            {"fields": ["time_phrase", "am_or_pm"]},
            "Australia/Melbourne",
        )


def test_explicit_short_draft_is_not_bound_to_unrelated_previous_turn():
    assert not _is_contextual_followup("Draft a reply to Alice about project X")
    assert _is_contextual_followup("Yes, draft that")


@pytest.mark.parametrize(
    ("text", "intent", "compound"),
    [
        ("Draft an email about free fries", "compose", False),
        ("Summarise the meeting email", "summarise", False),
        ("Write to customer support about my missing item", "compose", False),
        ("Can you help me respond confirming attendance?", "reply", False),
        ("Write back to them confirming Tuesday", "reply", False),
        ("Get back to Alex with a yes", "reply", False),
        ("Could you draft a reply here?", "reply", False),
        ("Could we meet tomorrow?", "plan_schedule", False),
        ("Suggest three times to meet next week", "plan_schedule", False),
        (
            "Summarise this, suggest three slots, and draft a reply",
            "plan_schedule",
            True,
        ),
    ],
)
def test_workflow_authorization_accepts_explicit_natural_requests(text, intent, compound):
    authorize_workflow(text, intent, compound)


@pytest.mark.parametrize(
    ("text", "intent", "compound"),
    [
        ("Does this receipt need a reply?", "reply", False),
        ("Should I reply?", "reply", False),
        ("Draft an email about free fries", "plan_schedule", False),
        ("Summarise the meeting email", "plan_schedule", False),
        ("Give me a summary of meeting availability", "plan_schedule", False),
        ("Draft a reply", "reply", True),
        ("Summarise this and draft a reply", "summarise", False),
    ],
)
def test_workflow_authorization_rejects_topic_words_and_incomplete_selection(
    text, intent, compound
):
    with pytest.raises(ValueError):
        authorize_workflow(text, intent, compound)


def test_workflow_bindings_keep_read_source_and_user_recipient_authority():
    with pytest.raises(ApiError) as missing_source:
        validate_workflow_bindings(
            PrepareWorkflow(intent="compose"),
            {"selected"},
            set(),
        )
    assert missing_source.value.code == "workflow_binding_invalid"

    with pytest.raises(ApiError) as source_recipient:
        validate_workflow_bindings(
            PrepareWorkflow(
                intent="compose",
                reference="selected",
                to_refs=["recipient-1"],
            ),
            {"selected"},
            set(),
        )
    assert source_recipient.value.code == "workflow_binding_invalid"

    validate_workflow_bindings(
        PrepareWorkflow(intent="compose", reference="selected"),
        {"selected"},
        set(),
    )
    validate_workflow_bindings(
        PrepareWorkflow(intent="compose", to_refs=["recipient-1"]),
        set(),
        {"recipient-1"},
    )


async def test_persistent_dialogue_encrypted_replay_no_duplicate_model(configured, db_sessionmaker):
    r = request()
    model = Model(tool("respond", kind="message", text="Hey!"))
    async with source_data.source_scope():
        first = await service.turn(1, r, factory=db_sessionmaker, model=model)
        replay = await service.turn(1, r, factory=db_sessionmaker, model=Model())
        r2 = r.model_copy(
            update={
                "request_id": str(uuid4()),
                "expected_version": 1,
                "instruction": "how are yo u",
            }
        )
        second_model = Model(tool("respond", kind="message", text="Ready to help."))
        await service.turn(1, r2, factory=db_sessionmaker, model=second_model)
    assert first["version"] == replay["version"] == 1
    assert second_model.contexts[0]["recent_dialogue"][0]["assistant"] == "Hey!"
    async with db_sessionmaker() as session:
        row = await session.get(Conversation, r.conversation_id)
        assert b"Hey!" not in row.state_enc
        assert row.version == 2
        assert await session.scalar(select(func.count()).select_from(Message)) == 0
    restored = await service.get(1, r.conversation_id, db_sessionmaker)
    assert restored["history"][0]["request_id"] == r.request_id


async def test_owner_version_and_busy_guards(configured, db_sessionmaker):
    r = request()
    async with db_sessionmaker.begin() as session:
        await store.claim(session, 1, r)
    for owner, error in [(1, "conversation_busy"), (2, "conversation_not_found")]:
        with pytest.raises(ApiError) as exc:
            async with db_sessionmaker.begin() as session:
                await store.claim(session, owner, r)
        assert exc.value.code == error
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await store.claim(session, 1, r.model_copy(update={"expected_version": 1}))
    assert exc.value.code == "conversation_version_conflict"


async def test_expired_lease_requires_same_request(configured, db_sessionmaker):
    r = request()
    async with db_sessionmaker.begin() as session:
        row, _, lease, _ = await store.claim(session, 1, r)
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await store.claim(session, 1, r.model_copy(update={"request_id": str(uuid4())}))
    assert exc.value.code == "conversation_retry_required"
    async with db_sessionmaker.begin() as session:
        _, _, new_lease, _ = await store.claim(session, 1, r)
    assert lease != new_lease


async def test_failed_retry_cannot_change_omitted_source_to_clear(configured, db_sessionmaker):
    r = request()
    async with db_sessionmaker.begin() as session:
        row, _, _, _ = await store.claim(session, 1, r)
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    changed = r.model_copy(update={"context_snapshot_id": None})
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await store.claim(session, 1, changed)
    assert exc.value.code == "idempotency_conflict"


async def test_user_admission_limits_parallel_conversations(
    configured, db_sessionmaker, monkeypatch
):
    monkeypatch.setenv("CONVERSATION_MAX_ACTIVE_PER_USER", "1")
    get_settings.cache_clear()
    async with db_sessionmaker.begin() as session:
        await store.claim(session, 1, request())
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await store.claim(session, 1, request())
    assert exc.value.status == 429 and exc.value.code == "conversation_capacity"
    monkeypatch.setenv("CONVERSATION_MAX_ACTIVE_PER_USER", "2")
    get_settings.cache_clear()


async def test_user_admission_limits_retained_turn_budget(configured, db_sessionmaker, monkeypatch):
    monkeypatch.setenv("CONVERSATION_MAX_RETAINED_TURNS", "20")
    get_settings.cache_clear()
    old = request()
    async with db_sessionmaker.begin() as session:
        row, _, _, _ = await store.claim(session, 1, old)
        row.version = 20
        row.lease_id = row.lease_until = None
        row.pending_request_id = row.pending_hash = None
    with pytest.raises(ApiError) as exc:
        async with db_sessionmaker.begin() as session:
            await store.claim(session, 1, request())
    assert exc.value.status == 429 and exc.value.code == "conversation_turn_limit"
    monkeypatch.setenv("CONVERSATION_MAX_RETAINED_TURNS", "250")
    get_settings.cache_clear()


async def test_retention_delete_and_account_change(configured, db_sessionmaker):
    r = request()
    await service.turn(
        1, r, factory=db_sessionmaker, model=Model(tool("respond", kind="message", text="Hey"))
    )
    async with db_sessionmaker.begin() as session:
        user = await session.get(User, 1)
        user.google_account_version += 1
    with pytest.raises(ApiError) as exc:
        await service.get(1, r.conversation_id, db_sessionmaker)
    assert exc.value.code == "conversation_account_changed"
    async with db_sessionmaker.begin() as session:
        row = await session.get(Conversation, r.conversation_id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await store.purge(session)
    async with db_sessionmaker() as session:
        assert await session.get(Conversation, r.conversation_id) is None


async def test_purge_keeps_expired_conversation_during_active_lease(configured, db_sessionmaker):
    r = request()
    async with db_sessionmaker.begin() as session:
        row, _, _, _ = await store.claim(session, 1, r)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        row.lease_until = datetime.now(UTC) + timedelta(seconds=30)
        await store.purge(session)
    async with db_sessionmaker() as session:
        assert await session.get(Conversation, r.conversation_id) is not None


async def test_search_to_second_turn_reads_reference_not_local_mail(configured, db_sessionmaker):
    r = request().model_copy(update={"instruction": "Find latest agenda email"})
    model = Model(
        tool("search_mail", query="agenda"),
        tool("respond", kind="message", text="Here are the latest matches."),
    )
    async with source_data.source_scope():
        found = await service.turn(1, r, factory=db_sessionmaker, model=model)
    assert found["search"]["results"][0]["message_id"] == MID
    async with db_sessionmaker() as session:
        state = store.decode(await session.get(Conversation, r.conversation_id))
        assert state["refs"]["mail-1"]["thread_id"] == TID
        assert TEXT not in json.dumps(state)
        assert "sender@example.test" not in json.dumps(state)
    r2 = r.model_copy(
        update={
            "instruction": "What's in the first one?",
            "request_id": str(uuid4()),
            "expected_version": 1,
        }
    )
    model2 = Model(
        tool("read_email", reference="mail-1"),
        tool(
            "respond",
            kind="message",
            text="Review the agenda by Friday.",
            evidence=[
                {"reference": "mail-1", "quote": "Please review the revised agenda by Friday."}
            ],
        ),
    )
    async with source_data.source_scope():
        result = await service.turn(1, r2, factory=db_sessionmaker, model=model2)
    assert result["kind"] == "message"
    assert model2.contexts[0]["displayed_result_order"] == ["mail-1"]


async def test_injection_cannot_choose_arbitrary_reference_or_search_literal(
    configured, db_sessionmaker
):
    r = request().model_copy(update={"instruction": "Find agenda"})
    model = Model(
        tool("read_email", reference="another-users-email"),
        tool("search_mail", query="passwords"),
        tool("respond", kind="clarification", text="Which email do you mean?"),
    )
    async with source_data.source_scope():
        result = await service.turn(1, r, factory=db_sessionmaker, model=model)
    assert [t["status"] for t in result["trace"][:2]] == ["invalid", "invalid"]
    assert configured.calls == []


async def test_source_cannot_authorize_a_workflow_the_user_only_asked_about(
    configured, db_sessionmaker
):
    r = request().model_copy(
        update={"instruction": "Find agenda and tell me whether it needs a reply"}
    )
    model = Model(
        tool("search_mail", query="agenda"),
        tool("read_email", reference="mail-1"),
        tool("prepare_workflow", intent="reply", reference="mail-1"),
        tool(
            "respond",
            kind="recommendation",
            text="Review the agenda by Friday before deciding whether to reply.",
            evidence=[{"reference": "mail-1", "quote": "review the revised agenda by Friday"}],
        ),
    )
    async with source_data.source_scope():
        result = await service.turn(1, r, factory=db_sessionmaker, model=model)
    assert result["trace"][2] == {"tool": "prepare_workflow", "status": "invalid"}
    assert result["kind"] == "recommendation"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantTask)) == 0


async def test_source_compose_keeps_provenance_and_rejects_source_recipient(
    configured, db_sessionmaker
):
    configured.text = (
        "Order 2241 confirmed. One item is missing. Contact support@example.test for help."
    )
    async with source_data.source_scope():
        await source_data.fetch(1, TID)
        async with db_sessionmaker.begin() as session:
            context = await source_data.capture(session, 1, TID, message_id=MID)
        r = request(context_snapshot_id=context.id).model_copy(
            update={
                "instruction": (
                    "One item is missing. Draft a message to customer support asking for help."
                )
            }
        )
        result = await service.turn(
            1,
            r,
            factory=db_sessionmaker,
            model=Model(
                tool("read_email", reference="selected"),
                tool("prepare_workflow", intent="compose"),
                tool(
                    "prepare_workflow",
                    intent="compose",
                    reference="selected",
                    to_refs=["recipient-1"],
                ),
                tool("prepare_workflow", intent="compose", reference="selected"),
            ),
        )

    assert [step["status"] for step in result["trace"]] == [
        "ok",
        "workflow_binding_invalid",
        "workflow_binding_invalid",
        "ok",
    ]
    assert result["task"]["context_snapshot_id"] == context.id
    assert result["task"]["draft_input"] is None


def test_api_context_ownership_and_feature_gate(configured, db_client, auth_headers, monkeypatch):
    r = request()
    monkeypatch.setattr(
        engine, "ConversationModel", lambda: Model(tool("respond", kind="message", text="Hey"))
    )
    res = db_client.post(
        "/assistant/conversation-turns", headers=auth_headers(1), json=r.model_dump()
    )
    assert res.status_code == 200, res.text
    assert (
        db_client.get(
            f"/assistant/conversations/{r.conversation_id}", headers=auth_headers(2)
        ).status_code
        == 404
    )
    assert (
        db_client.delete(
            f"/assistant/conversations/{r.conversation_id}", headers=auth_headers(1)
        ).status_code
        == 200
    )
    assert (
        db_client.get(
            f"/assistant/conversations/{r.conversation_id}", headers=auth_headers(1)
        ).status_code
        == 404
    )
    assert (
        db_client.delete(
            f"/assistant/conversations/{r.conversation_id}", headers=auth_headers(1)
        ).status_code
        == 200
    )
    monkeypatch.setenv("CONVERSATION_ENABLED", "false")
    get_settings.cache_clear()
    assert (
        db_client.post(
            "/assistant/conversation-turns", headers=auth_headers(1), json=request().model_dump()
        ).status_code
        == 503
    )


async def test_prepare_workflow_submits_existing_job_and_retry_only_once(
    configured, db_sessionmaker
):
    r = request().model_copy(
        update={"instruction": "Write an email to alex@example.test thanking them for the update"}
    )
    model = Model(tool("prepare_workflow", intent="compose", to_refs=["recipient-1"]))
    async with source_data.source_scope():
        first = await service.turn(1, r, factory=db_sessionmaker, model=model)
        second = await service.turn(1, r, factory=db_sessionmaker, model=Model())
    assert first["task_id"] == second["task_id"]
    assert first["task"]["state"] == "queued"
    assert first["task"]["draft_input"]["to"] == ["alex@example.test"]


async def test_workflow_records_user_turn_and_model_release(configured, db_sessionmaker):
    r = request().model_copy(
        update={"instruction": "Write to alex@example.test and thank them for the update"}
    )
    model = Model(
        tool(
            "prepare_workflow",
            intent="compose",
            to_refs=["recipient-1"],
        )
    )
    async with source_data.source_scope():
        result = await service.turn(1, r, factory=db_sessionmaker, model=model)
    assert result["task"]["instruction"] == r.instruction
    async with db_sessionmaker() as session:
        event = await session.scalar(
            select(TaskEvent).where(
                TaskEvent.task_id == result["task_id"], TaskEvent.kind == "task.accepted"
            )
        )
    provenance = event.payload["conversation_provenance"]
    assert provenance["source"] == "conversation_user_turns"
    assert provenance["authority"] == "user_dialogue_only"
    assert provenance["release"].startswith("contextual-conversation-")
    assert "prompt_hash" in provenance and "tools_hash" in provenance
    assert provenance["provider"] == get_settings().inference_provider
    assert provenance["model_id"] == get_settings().bedrock_model_id


async def test_selected_source_is_owner_checked_before_model(configured, db_sessionmaker):
    r = request(context_snapshot_id=str(uuid4()))
    model = Model(tool("respond", kind="message", text="No"))
    async with source_data.source_scope():
        with pytest.raises(ApiError) as exc:
            await service.turn(1, r, factory=db_sessionmaker, model=model)
    assert exc.value.code == "context_not_found"
    assert model.contexts == []


async def test_search_read_then_summary_uses_reference_capture(configured, db_sessionmaker):
    r = request().model_copy(update={"instruction": "Find agenda and summarise it"})
    model = Model(
        tool("search_mail", query="agenda"),
        tool("read_email", reference="mail-1"),
        tool(
            "prepare_workflow",
            intent="summarise",
            reference="mail-1",
        ),
    )
    async with source_data.source_scope():
        result = await service.turn(1, r, factory=db_sessionmaker, model=model)
    assert result["task"]["context_snapshot_id"] is not None
    from app.db.models import ContextSnapshot

    async with db_sessionmaker() as session:
        capture = await session.get(ContextSnapshot, result["task"]["context_snapshot_id"])
        assert capture.payload["storage"] == "gmail-reference-1.0"
        assert capture.payload["ui_map"]["visible_message_ids"] == [MID]
        assert capture.payload["ui_map"]["selected_message_ids"] == [MID]
        assert TEXT not in json.dumps(capture.payload)
        assert await session.scalar(select(func.count()).select_from(Message)) == 0


async def test_context_clear_does_not_reuse_previous_pin(configured, db_sessionmaker):
    r = request()
    async with db_sessionmaker.begin() as session:
        row, state, lease, _ = await store.claim(session, 1, r)
        state["refs"]["selected"] = {"thread_id": TID, "context_id": "old"}
        await store.complete(session, 1, r, lease, state, {"kind": "message", "text": "Hey"})
    r2 = r.model_copy(
        update={"request_id": str(uuid4()), "expected_version": 1, "context_snapshot_id": None}
    )
    model = Model(tool("respond", kind="message", text="Hello"))
    await service.turn(1, r2, factory=db_sessionmaker, model=model)
    assert model.contexts[0]["selected_reference"] is None


async def test_active_task_clear_does_not_reuse_previous_work(configured, db_sessionmaker):
    r = request()
    async with db_sessionmaker.begin() as session:
        _, state, lease, _ = await store.claim(session, 1, r)
        state["active_task_id"] = str(uuid4())
        await store.complete(session, 1, r, lease, state, {"kind": "message", "text": "Hey"})
    r2 = r.model_copy(
        update={
            "request_id": str(uuid4()),
            "expected_version": 1,
            "active_task_id": None,
        }
    )
    model = Model(tool("respond", kind="message", text="Hello"))
    result = await service.turn(1, r2, factory=db_sessionmaker, model=model)
    assert result["text"] == "Hello"
    assert model.contexts[0]["active_work"] is None


async def test_delete_refuses_active_turn(configured, db_sessionmaker):
    r = request()
    async with db_sessionmaker.begin() as session:
        await store.claim(session, 1, r)
    with pytest.raises(ApiError) as exc:
        await service.remove(1, r.conversation_id, db_sessionmaker)
    assert exc.value.code == "conversation_busy"


async def test_crash_after_task_creation_recovers_checkpoint(
    configured, db_sessionmaker, monkeypatch
):
    r = request().model_copy(
        update={"instruction": "Compose an email to alex@example.test thanking them"}
    )
    original = store.complete

    async def crash(*args, **kwargs):
        raise RuntimeError("Simulated API crash after committed task")

    monkeypatch.setattr(store, "complete", crash)
    async with source_data.source_scope():
        with pytest.raises(RuntimeError):
            await service.turn(
                1,
                r,
                factory=db_sessionmaker,
                model=Model(
                    tool(
                        "prepare_workflow",
                        intent="compose",
                        to_refs=["recipient-1"],
                    )
                ),
            )
    monkeypatch.setattr(store, "complete", original)
    async with source_data.source_scope():
        recovered = await service.turn(1, r, factory=db_sessionmaker, model=Model())
    assert recovered["kind"] == "task" and recovered["version"] == 1
    from app.db.models import AssistantTask

    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(AssistantTask)) == 1


async def test_failure_after_proposal_reservation_resumes_saved_plan(
    configured, db_sessionmaker, monkeypatch
):
    from app.assistant import coordinator

    r = request().model_copy(
        update={
            "instruction": "Suggest three meeting times tomorrow and draft a reply",
        }
    )

    async def interrupt(_row):
        raise RuntimeError("Simulated interruption after proposal reservation")

    monkeypatch.setattr(coordinator, "interpret", interrupt)
    with pytest.raises(RuntimeError):
        await service.turn(
            1,
            r,
            factory=db_sessionmaker,
            model=Model(
                tool(
                    "prepare_workflow",
                    intent="plan_schedule",
                    compound=True,
                )
            ),
        )
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(CommandPlan)) == 1

    async def recover(_row):
        return "failed", {"reason": "synthetic_recovery"}

    monkeypatch.setattr(coordinator, "interpret", recover)
    recovered = await service.turn(1, r, factory=db_sessionmaker, model=Model())
    assert recovered["kind"] == "proposal" and recovered["version"] == 1
    assert recovered["proposal"]["state"] == "failed"
    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(CommandPlan)) == 1
    restored = await service.get(1, r.conversation_id, db_sessionmaker)
    assert restored["active_proposal_id"] == recovered["proposal_id"]
    assert restored["proposal"]["plan_id"] == recovered["proposal_id"]
    assert restored["history"][0]["proposal_id"] == recovered["proposal_id"]
    followup = r.model_copy(
        update={
            "request_id": str(uuid4()),
            "expected_version": 1,
            "instruction": "What is waiting for my review?",
            "active_task_id": None,
        }
    )
    followup_model = Model(tool("respond", kind="message", text="The proposal awaits review."))
    await service.turn(1, followup, factory=db_sessionmaker, model=followup_model)
    assert followup_model.contexts[0]["active_work"]["proposal_id"] == recovered["proposal_id"]
    replacement = r.model_copy(
        update={
            "request_id": str(uuid4()),
            "expected_version": 2,
            "instruction": "Write an email to alex@example.test thanking them",
            "active_task_id": None,
        }
    )
    replacement_result = await service.turn(
        1,
        replacement,
        factory=db_sessionmaker,
        model=Model(tool("prepare_workflow", intent="compose", to_refs=["recipient-1"])),
    )
    restored = await service.get(1, r.conversation_id, db_sessionmaker)
    assert restored["active_task_id"] == replacement_result["task_id"]
    assert restored["active_proposal_id"] is None and restored["proposal"] is None


async def test_recipient_roles_are_reference_bound(configured, db_sessionmaker):
    r = request().model_copy(
        update={
            "instruction": (
                "Compose to alex@example.test, cc bob@example.test and "
                "bcc cathy@example.test: thanks"
            )
        }
    )
    async with source_data.source_scope():
        result = await service.turn(
            1,
            r,
            factory=db_sessionmaker,
            model=Model(
                tool(
                    "prepare_workflow",
                    intent="compose",
                    to_refs=["recipient-1"],
                    cc_refs=["recipient-2"],
                    bcc_refs=["recipient-3"],
                )
            ),
        )
    assert result["task"]["draft_input"]["to"] == ["alex@example.test"]
    assert result["task"]["draft_input"]["cc"] == ["bob@example.test"]
    assert result["task"]["draft_input"]["bcc"] == ["cathy@example.test"]
