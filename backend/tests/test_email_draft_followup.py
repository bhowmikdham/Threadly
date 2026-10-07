"""Bounded production-shape replay: real state/engine, fake model and Gmail HTTP."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.actions import gmail_draft
from app.api.errors import ApiError
from app.config import get_settings
from app.conversation import email_draft, service, store
from app.db.models import (
    ActionApproval,
    AssistantAction,
    AssistantTask,
    Conversation,
    GmailDraftSave,
    User,
)
from app.schemas.gmail_draft import CreateGmailDraft
from tests.test_conversation import Model, configured, request, tool  # noqa: F401
from tests.test_gmail_drafts import provider, token  # noqa: F401
from tests.test_on_demand_gmail import setup  # noqa: F401

# Synthetic identities; the exact private incident transcript stays outside Git.
INITIAL = "could you create a draft for an email to Alex person@example.test"
MESSAGE = "hey dad how are you doing"
DRAFT = {
    "subject": "Hello",
    "body": "Hey dad, how are you doing?",
    "unresolved_fields": [],
    "sources": [],
}


def next_turn(turn, text):
    return turn.model_copy(
        update={
            "request_id": str(uuid4()),
            "expected_version": turn.expected_version + 1,
            "instruction": text,
        }
    )


async def seed(factory):
    first = request().model_copy(update={"instruction": INITIAL})
    result = await service.turn(
        1, first, factory=factory, model=Model(tool("prepare_email_draft", recipient="Alex"))
    )
    assert result["text"] == "What would you like to say?"
    return first


async def draft(factory):
    turn = next_turn(await seed(factory), MESSAGE)
    result = await service.turn(
        1,
        turn,
        factory=factory,
        model=Model(
            tool(
                "prepare_email_draft",
                continue_previous=True,
                recipient="Alex",
                purpose=MESSAGE,
                draft=DRAFT,
            )
        ),
    )
    return turn, result


@pytest.mark.usefixtures("configured")
async def test_complete_followup_chain_keeps_structured_draft_and_never_writes(
    db_sessionmaker, monkeypatch
):
    monkeypatch.setenv("EMAIL_WRITES_ENABLED", "false")
    get_settings.cache_clear()
    turn, result = await draft(db_sessionmaker)
    identifier = result["email_draft"]["draft_id"]
    assert result["email_draft"]["recipient"] == "Alex"
    for text in (
        "yes please send this draft or create this craft",
        "yes",
        "yep thanks please do that",
    ):
        turn = next_turn(turn, text)
        # No model call is needed for guidance, and an empty model would fail if invoked.
        result = await service.turn(1, turn, factory=db_sessionmaker, model=Model())
        assert result["active_email_draft_id"] == identifier
        assert result["email_draft_review"]["chat_can_save"] is False
        assert result["email_draft_review"]["save_status"] == "not_saved"
        assert "What would you like" not in result["text"]
    restored = await service.get(1, turn.conversation_id, db_sessionmaker)
    assert restored["active_email_draft_id"] == identifier
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, turn.conversation_id))
        assert state[email_draft.KEY]["purpose"] == MESSAGE
        assert state[email_draft.KEY]["recipient_roles"]["to"] == []
        assert state[email_draft.KEY]["draft"]["body"] == DRAFT["body"]
        for table in (GmailDraftSave, AssistantAction, ActionApproval, AssistantTask):
            assert await db.scalar(select(func.count()).select_from(table)) == 0


@pytest.mark.usefixtures("configured")
async def test_repair_repeats_retained_fields_and_blocks_prose_fallback(db_sessionmaker):
    turn = next_turn(await seed(db_sessionmaker), MESSAGE)

    class Observing(Model):
        messages = None

        async def decide(self, system, messages, tools):
            self.messages = deepcopy(messages)
            return await super().decide(system, messages, tools)

    model = Observing(
        tool("prepare_email_draft", continue_previous=True, recipient="Alex", purpose=MESSAGE),
        tool(
            "respond", kind="message", text="Here is your draft: Hello, hey dad how are you doing?"
        ),
        tool("prepare_email_draft", continue_previous=True, draft=DRAFT),
    )
    result = await service.turn(1, turn, factory=db_sessionmaker, model=model)
    assert result["email_draft"]["body"] == DRAFT["body"]
    assert result["trace"][0]["reason"] == "draft_text_required"
    assert result["trace"][1]["reason"] == "email_draft_required"
    repair = model.messages[2]["content"][0]["toolResult"]["content"][0]["json"]
    assert repair["pending_email_draft"]["purpose"] == MESSAGE
    assert "read_email" not in repair["message"]
    assert MESSAGE not in str(result["trace"])


@pytest.mark.usefixtures("configured")
async def test_failed_generation_keeps_details_and_retry_uses_them(db_sessionmaker):
    turn = next_turn(await seed(db_sessionmaker), MESSAGE)
    bad = tool("prepare_email_draft", continue_previous=True, purpose=MESSAGE)
    result = await service.turn(1, turn, factory=db_sessionmaker, model=Model(*([bad] * 8)))
    assert result["error_code"] == "email_draft_not_prepared"
    turn = next_turn(turn, "try again")
    result = await service.turn(
        1,
        turn,
        factory=db_sessionmaker,
        model=Model(tool("prepare_email_draft", continue_previous=True, draft=DRAFT)),
    )
    assert result["email_draft"]["body"] == DRAFT["body"]


@pytest.mark.usefixtures("configured")
async def test_legacy_prose_only_answer_recovers_user_message_not_assistant_claim(db_sessionmaker):
    turn = await seed(db_sessionmaker)
    async with db_sessionmaker.begin() as db:
        row = await db.get(Conversation, turn.conversation_id)
        state = store.decode(row)
        state["history"].append(
            {
                "user": MESSAGE,
                "assistant": "I saved an invented draft for SomeoneElse.",
                "kind": "message",
                "request_id": str(uuid4()),
                "version": 2,
            }
        )
        row.version = 2
        row.state_enc = store.encode(state)
    turn = next_turn(next_turn(turn, MESSAGE), "prepare it again")
    result = await service.turn(
        1,
        turn,
        factory=db_sessionmaker,
        model=Model(
            tool("prepare_email_draft", continue_previous=True, purpose=MESSAGE, draft=DRAFT)
        ),
    )
    assert result["email_draft"]["recipient"] == "Alex"
    assert result["email_draft"]["body"] == DRAFT["body"]


@pytest.mark.usefixtures("configured")
@pytest.mark.parametrize("permission", ["ready", "scope_missing", "reconnect_required"])
async def test_truthful_permission_and_stale_client_guidance(db_sessionmaker, permission):
    async with db_sessionmaker.begin() as db:
        user = await db.get(User, 1)
        user.google_scopes = (
            ["https://www.googleapis.com/auth/gmail.compose"] if permission == "ready" else []
        )
        if permission == "reconnect_required":
            user.google_connected = False
    turn, _ = await draft(db_sessionmaker)
    turn = next_turn(turn, "I cannot see the draft button")
    result = await service.turn(
        1, turn, factory=db_sessionmaker, model=Model(tool("review_email_draft"))
    )
    assert result["email_draft_review"]["installed_client_verified"] is False
    assert "update/reload" in result["text"]
    if permission != "ready":
        assert "Enable draft creation" in result["text"]
    assert "Create draft" in result["text"]


@pytest.mark.usefixtures("configured", "token")
@pytest.mark.parametrize(
    "outcome,status",
    [("success", "succeeded"), ("rejected", "failed"), ("timeout", "outcome_unknown")],
)
async def test_deliberate_click_after_followup_has_owned_receipt_and_no_duplicate(
    db_sessionmaker, outcome, status
):
    async with db_sessionmaker.begin() as db:
        user = await db.get(User, 1)
        user.google_scopes = ["https://www.googleapis.com/auth/gmail.compose"]
        version = user.google_account_version
        address = user.email
    turn, result = await draft(db_sessionmaker)
    identifier = result["email_draft"]["draft_id"]
    turn = next_turn(turn, "save this draft")
    await service.turn(1, turn, factory=db_sessionmaker, model=Model())
    # Only this explicit simulated card click invokes a mock Gmail POST.
    body = CreateGmailDraft(
        request_id="deliberate-click",
        conversation_id=turn.conversation_id,
        expected_version=turn.expected_version + 1,
        expected_revision=1,
        draft_id=identifier,
        from_address=address,
        account_version=version,
        recipients={"to": ["confirmed@example.test"]},
        subject="Edited subject",
        body="Edited body",
        unresolved_fields=[],
    )
    calls = []
    async with db_sessionmaker() as db:
        saved = await gmail_draft.create(db, 1, body, transport=provider(calls, outcome))
    assert saved["state"] == status and len(calls) == 1
    for instruction in ("yes please save this draft", "yes", "yep thanks please do that"):
        turn = next_turn(turn, instruction)
        result = await service.turn(1, turn, factory=db_sessionmaker, model=Model())
        assert result["email_draft_review"]["save_status"] == status
    assert len(calls) == 1
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantAction)) == 0
    with pytest.raises(ApiError) as exc:
        await service.turn(2, next_turn(turn, "yes"), factory=db_sessionmaker, model=Model())
    assert exc.value.code == "conversation_not_found"


@pytest.mark.usefixtures("configured")
async def test_new_goal_supersedes_active_card_and_does_not_inherit_fields(db_sessionmaker):
    turn, old = await draft(db_sessionmaker)
    turn = next_turn(turn, "Write another email to Priya")
    result = await service.turn(
        1,
        turn,
        factory=db_sessionmaker,
        model=Model(tool("prepare_email_draft", recipient="Priya")),
    )
    assert result["active_email_draft_id"] is None
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, turn.conversation_id))
        assert email_draft.current_text_draft(state) is None
        assert state[email_draft.KEY]["purpose"] == ""
    turn = next_turn(turn, "thanks for your help")
    result = await service.turn(
        1,
        turn,
        factory=db_sessionmaker,
        model=Model(
            tool(
                "prepare_email_draft",
                continue_previous=True,
                purpose="thanks for your help",
                draft=DRAFT,
            )
        ),
    )
    assert result["active_email_draft_id"] != old["active_email_draft_id"]


@pytest.mark.usefixtures("configured")
async def test_failed_revision_keeps_existing_draft_and_gratitude_does_not_repeat_controls(
    db_sessionmaker,
):
    turn, original = await draft(db_sessionmaker)
    turn = next_turn(turn, "Make it shorter")
    result = await service.turn(
        1,
        turn,
        factory=db_sessionmaker,
        model=Model(*([tool("prepare_email_draft", continue_previous=True)] * 8)),
    )
    assert result["active_email_draft_id"] == original["active_email_draft_id"]
    turn = next_turn(turn, "save this draft")
    await service.turn(1, turn, factory=db_sessionmaker, model=Model())
    turn = next_turn(turn, "thanks")
    result = await service.turn(
        1,
        turn,
        factory=db_sessionmaker,
        model=Model(tool("respond", kind="message", text="You're welcome.")),
    )
    assert "Create draft" not in result["text"]


def test_compound_goals_and_quoted_body_do_not_short_circuit_into_save_guidance():
    from app.conversation import email_review
    from tests.test_email_draft_clarification import runtime

    for text in (
        "Save this draft and book an event tomorrow at 2 pm",
        "Write another email to Alex saying save this draft",
        "Summarise the email that says 'save this draft'",
    ):
        r = runtime(text, {email_draft.KEY: {"status": "drafted"}})
        assert not email_review.requested(r)
