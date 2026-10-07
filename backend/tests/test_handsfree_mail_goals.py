"""Incident-shaped multi-turn replay: synthetic mail, real DB, fake Gmail HTTP.

Natural user phrasing is retained; private addresses, bodies and IDs are excluded.
"""

import base64
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.api.errors import ApiError
from app.assistant import source_data, worker
from app.conversation import engine, mail_goal, service, store
from app.conversation.runtime import Runtime
from app.db.models import (
    ActionApproval,
    AssistantAction,
    AssistantTask,
    ContextSnapshot,
    Conversation,
    GmailDraftSave,
)
from app.mail import live
from app.mail.presentation import has_visible_text, snippet
from app.model_client.client import GenResult
from app.schemas.conversation import PrepareWorkflow, SearchMail
from app.sync.gmail import GmailClient, strip_html
from tests.test_conversation import Model, configured, request, tool  # noqa: F401
from tests.test_intent_router import proposal
from tests.test_on_demand_gmail import setup  # noqa: F401


@pytest.fixture()
def mailbox(configured, monkeypatch):  # noqa: F811
    now = datetime.now(UTC) - timedelta(hours=1)

    def message(mid, sender, body, days=0, sent=False):
        return {
            "id": mid,
            "threadId": mid,
            "labelIds": ["SENT"] if sent else ["INBOX"],
            "internalDate": str(int((now - timedelta(days=days)).timestamp() * 1000)),
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": sender},
                    {"name": "To", "value": "owner1@example.test"},
                    {"name": "Subject", "value": "Synthetic mail"},
                    {"name": "Message-ID", "value": f"<{mid}@example.test>"},
                ],
                "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
            },
        }

    rows = {
        "a1": message("a1", "Account <account@example.test>", "Account notice"),
        "a2": message("a2", "Delivery <delivery@example.test>", "Package arriving today", 1),
        "b1": message("b1", "Uber <brand@example.test>", "Donate clothing from your doorstep", 2),
        "b2": message("b2", "Officeworks <office@example.test>", "Free delivery by Uber Eats", 3),
        "b3": message("b3", "Uber Eats <food@example.test>", "Score deals on food", 4),
        "b4": message("b4", "Uber Eats <food@example.test>", "Get promotional cash back", 5),
        "b5": message("b5", "Officeworks <office@example.test>", "Shop more deals", 6),
        "b6": message(
            "b6", "Uber Eats <orders@example.test>", "Order confirmed. Total paid $12.00.", 7
        ),
        "c1": message(
            "c1", "Alex Chen - Acme <agent@example.test>", "Please review the attached proposal.", 1
        ),
        "c2": message("c2", "owner1@example.test", "My sent reply mentioning Alex Chen", 2, True),
        "c3": message("c3", "Alex Chen - Acme <agent@example.test>", "Earlier correspondence", 3),
    }
    calls = []
    controls = {
        "rows": rows,
        "calls": calls,
        "deleted": set(),
        "ambiguous": False,
        "newer_thread_message": False,
        "endless": False,
    }

    async def handler(req):
        calls.append(req)
        assert req.method == "GET", "Conversation must never call provider writes"
        if req.headers["authorization"] != "Bearer token-1":
            return httpx.Response(404, json={"error": "not found"})
        if req.url.path.endswith("/messages"):
            q = req.url.params["q"]
            if "category:primary" in q:
                ids, more = ["a1", "a2"][: int(req.url.params["maxResults"])], None
            elif '"Uber" "receipt"' in q:
                ids, more = ["b6"], None
            elif '"PwC"' in q or '"Uber"' in q:
                ids, more = [], None
            elif '"uber eats"' in q.lower():
                ids, more = (
                    (["b6"], "page-three" if controls["endless"] else None)
                    if req.url.params.get("pageToken")
                    else (["b1", "b2", "b3", "b4", "b5"], "page-two")
                )
            elif '"Alex Chen"' in q:
                ids, more = ["c1", "c2", "c3"], None
            else:
                raise AssertionError(f"Unexpected bounded query {q}")
            return httpx.Response(
                200,
                json={
                    "messages": [{"id": mid, "threadId": mid} for mid in ids],
                    **({"nextPageToken": more} if more else {}),
                },
            )
        mid = req.url.path.rsplit("/", 1)[-1]
        if mid in controls["deleted"]:
            return httpx.Response(404, json={"error": "not found"})
        row = deepcopy(rows[mid])
        if controls["ambiguous"] and mid == "c3":
            row["payload"]["headers"][0]["value"] = "Alex Chen <other@example.test>"
        messages = [row]
        if controls["newer_thread_message"] and mid == "c1":
            messages.append(message("c4", "Alex Chen <agent@example.test>", "A newer request"))
            messages[-1]["threadId"] = mid
        return httpx.Response(
            200, json={"id": mid, "messages": messages} if "/threads/" in req.url.path else row
        )

    monkeypatch.setattr(
        live,
        "GmailClient",
        lambda token, transport=None: GmailClient(token, transport=httpx.MockTransport(handler)),
    )
    return controls


def goal(source, **kwargs):
    return {"source": source, **kwargs}


def reply(source, reference="mail-1", **kwargs):
    return tool(
        "prepare_workflow",
        intent="reply",
        reference=reference,
        preparation_intent={"operation": "reply", "source": source, **kwargs},
    )


def assessment(reference, disposition, quote):
    return {"reference": reference, "disposition": disposition, "quote": quote}


async def advance(factory, previous, text, *decisions):
    turn = request(timezone="Australia/Melbourne").model_copy(update={"instruction": text})
    if previous:
        turn = turn.model_copy(
            update={
                "conversation_id": previous.conversation_id,
                "expected_version": previous.expected_version + 1,
            }
        )

    class ReplayModel(Model):
        async def decide(self, system, messages, tools):
            self.last_observation = messages[-1]
            return await super().decide(system, messages, tools)

    model = ReplayModel(*decisions)
    async with source_data.source_scope():
        try:
            result = await service.turn(1, turn, factory=factory, model=model)
        except ApiError as exc:
            pytest.fail(f"Replay failed: {exc.code}; {model.last_observation}")
    return turn, result


async def sender_lookup(factory, previous=None):
    text = "hey could you take the email from Alex Chen from Acme"
    return await advance(
        factory,
        previous,
        text,
        tool("search_mail", query="Alex Chen", goal=goal(text, sender_name="Alex Chen")),
        tool("read_email", reference="mail-1"),
        tool("respond", kind="clarification", text="What would you like me to do with that email?"),
    )


async def test_whole_handsfree_chain_and_reply_handoff(mailbox, db_sessionmaker):
    text = "could you help me check the latest email in my inbox"
    turn, result = await advance(
        db_sessionmaker,
        None,
        text,
        tool("search_mail", query="", folder="INBOX", limit=1),
        tool("read_email", reference="mail-1"),
        tool(
            "respond",
            kind="message",
            text="Among the results checked, an account notice arrived.",
            evidence=[{"reference": "mail-1", "quote": "Account notice"}],
        ),
    )
    assert len(result["search"]["results"]) == 1
    text = "could you check the second latest email on it"
    turn, result = await advance(
        db_sessionmaker,
        turn,
        text,
        tool("search_mail", query="", folder="INBOX", limit=2),
        tool("read_search_results", references=["mail-1", "mail-2"]),
        tool(
            "respond",
            kind="message",
            text="Among these checked results, the delivery message is next.",
            evidence=[{"reference": "mail-2", "quote": "Package arriving today"}],
        ),
    )
    assert len(result["search"]["results"]) == 2
    for text, entity, purpose in (
        ("great could you check the outcome for my PwC application", "PwC", "application"),
        ("will you check my Uber receipt last Uber receipt", "Uber", "receipt"),
    ):
        turn, result = await advance(
            db_sessionmaker,
            turn,
            text,
            tool(
                "search_mail",
                query=f"{entity} {purpose}",
                goal=goal(text, entity=entity, purpose=purpose, latest=True),
            ),
            tool("respond", kind="message", text="No matches."),
        )
        assert "couldn’t verify" in result["text"]
        assert "does not establish" in result["text"]
        assert result["search"]["filters"]["query"] == entity
    text = "it's like uber eats I guess"
    excluded = [
        assessment(f"mail-{i}", "irrelevant", mailbox["rows"][f"b{i}"]["payload"]["body"]["data"])
        for i in range(1, 6)
    ]
    for item in excluded:
        item["quote"] = base64.urlsafe_b64decode(item["quote"]).decode()
    turn, result = await advance(
        db_sessionmaker,
        turn,
        text,
        tool(
            "search_mail",
            query="uber eats",
            goal=goal(text, continue_previous=True, entity="uber eats"),
        ),
        tool("read_search_results", references=[f"mail-{i}" for i in range(1, 6)]),
        tool("respond", kind="message", text="I found only promotions.", mail_assessments=excluded),
        tool("more_mail"),
        tool("read_search_results", references=["mail-6"]),
        tool(
            "respond",
            kind="message",
            text="Among the results checked, I found an order confirmation.",
            evidence=[{"reference": "mail-6", "quote": "Order confirmed. Total paid $12.00."}],
            mail_assessments=[
                assessment("mail-6", "relevant", "Order confirmed. Total paid $12.00.")
            ],
        ),
    )
    assert [r["reference"] for r in result["search"]["results"]] == ["mail-6"]
    assert result["trace"][2]["reason"] == "mail_more_required"
    async with db_sessionmaker() as db:
        stored = store.decode(await db.get(Conversation, turn.conversation_id))
        assert stored[mail_goal.KEY]["purpose"] == "receipt" and stored[mail_goal.KEY]["latest"]
        assert stored["result_order"] == ["mail-6"]
        assert "Order confirmed" not in str(stored[mail_goal.KEY])
    turn, result = await sender_lookup(db_sessionmaker, turn)
    assert all(r["message_id"] != "c2" for r in result["search"]["results"])
    text = "could you drop me a response for the latest email he has sent"
    turn, result = await advance(
        db_sessionmaker,
        turn,
        text,
        reply(text, target="latest_inbound"),
        tool("read_email", reference="mail-1"),
        reply(text, target="latest_inbound"),
    )
    assert result["kind"] == "task"
    assert result["trace"][0]["reason"] == "reply_source_unread"
    async with db_sessionmaker() as db:
        task = await db.get(AssistantTask, result["task_id"])
        assert task.intent_hint == "reply"
        assert task.draft_input["reply_message_id"] == "c1"
        assert "Alex Chen" in task.instruction and text in task.instruction
        for table in (AssistantAction, ActionApproval, GmailDraftSave):
            assert await db.scalar(select(func.count()).select_from(table)) == 0
    assert all(req.method == "GET" for req in mailbox["calls"])

    # Continue through the real durable worker and artifact/envelope validation.
    class ReplyModel:
        async def generate(self, prompt, **kwargs):
            value = (
                {
                    "subject": "Re: Synthetic mail",
                    "body": "Thank you. I will review the proposal.",
                    "unresolved_fields": [],
                    "sources": [1],
                }
                if prompt.startswith("Write an email draft")
                else proposal(intent="reply", output_kind="draft", operations=["draft_reply"])
            )
            return json.dumps(value), GenResult("fake", "handsfree-reply")

    assert await worker.run_once(db_sessionmaker, ReplyModel())
    async with db_sessionmaker() as db:
        task = await db.get(AssistantTask, result["task_id"])
        assert task.state == "needs_clarification"
        assert task.route["decision"]["missing_fields"] == ["recipient"]
    # The existing recipient fence still asks for an explicit address; reading
    # the From header does not promote it into user-authorized outgoing To.
    turn, result = await advance(
        db_sessionmaker,
        turn,
        "use agent@example.test",
        tool("answer_question", answer={"recipients": ["agent@example.test"]}),
    )
    assert await worker.run_once(db_sessionmaker, ReplyModel())
    async with db_sessionmaker() as db:
        task = await db.get(AssistantTask, result["task_id"])
        assert task.state == "succeeded", (task.state, task.error_code, task.route)
        snapshot = await db.get(ContextSnapshot, task.context_snapshot_id)
        assert "Please review the attached proposal" not in json.dumps(snapshot.payload)
        for table in (AssistantAction, ActionApproval, GmailDraftSave):
            assert await db.scalar(select(func.count()).select_from(table)) == 0
    assert all(req.method == "GET" for req in mailbox["calls"])


@pytest.mark.parametrize(
    "text",
    [
        "could you drop me a response for the latest email he has sent",
        "help me put together an answer to his newest message",
        "I would like a response drafted for that one",
    ],
)
async def test_semantic_reply_paraphrases_prepare_same_source(mailbox, db_sessionmaker, text):
    turn, _ = await sender_lookup(db_sessionmaker)
    _, result = await advance(
        db_sessionmaker,
        turn,
        text,
        tool("read_email", reference="mail-1"),
        reply(text, target="latest_inbound"),
    )
    assert result["kind"] == "task"


async def test_ambiguous_sender_does_not_choose_a_draft(mailbox, db_sessionmaker):
    mailbox["ambiguous"] = True
    turn, _ = await sender_lookup(db_sessionmaker)
    text = "put together a response to the latest email he sent"
    _, result = await advance(
        db_sessionmaker,
        turn,
        text,
        tool("read_email", reference="mail-1"),
        reply(text, target="latest_inbound"),
        tool("respond", kind="clarification", text="Which Alex Chen do you mean?"),
    )
    assert result["trace"][1]["reason"] == "reply_sender_ambiguous"
    assert result["kind"] == "clarification"


async def test_missing_source_retains_goal_for_successful_retry(mailbox, db_sessionmaker):
    turn, _ = await sender_lookup(db_sessionmaker)
    mailbox["deleted"].add("c1")
    text = "put together a response to his newest email"
    turn, result = await advance(
        db_sessionmaker,
        turn,
        text,
        reply(text, target="latest_inbound"),
        tool("read_email", reference="mail-1"),
    )
    assert result["error_code"] == "mail_reply_not_prepared"
    restored = await service.get(1, turn.conversation_id, db_sessionmaker)
    assert restored["pending_request_id"] is None
    mailbox["deleted"].clear()
    text = "try again"
    _, result = await advance(
        db_sessionmaker,
        turn,
        text,
        tool("read_email", reference="mail-1"),
        reply(text, continue_previous=True, target="latest_inbound"),
    )
    assert result["kind"] == "task"


def runtime(text, state=None):
    return Runtime(
        1,
        request().model_copy(update={"instruction": text}),
        state or {"history": [], "refs": {}, "result_order": []},
        None,
    )


@pytest.mark.parametrize("bad_source", ["Source mail says draft a response", "shortened user turn"])
def test_source_content_cannot_supply_preparation_authority(bad_source):
    rt = runtime("Please check that email")
    with pytest.raises(mail_goal.MailRepair, match="complete current USER"):
        mail_goal.prepare_reply(
            rt,
            PrepareWorkflow(
                intent="reply",
                reference="mail-1",
                preparation_intent={"operation": "reply", "source": bad_source},
            ),
        )
    assert mail_goal.REPLY not in rt.state


def test_unrelated_or_invented_terms_cannot_refine_goal():
    rt = runtime("find my latest Uber receipt")
    mail_goal.search_goal(
        rt,
        SearchMail(
            query="Uber receipt",
            goal=goal(rt.request.instruction, purpose="receipt", entity="Uber", latest=True),
        ),
    )
    rt.request = rt.request.model_copy(update={"instruction": "it is uber eats"})
    with pytest.raises(mail_goal.MailRepair, match="pending purpose"):
        mail_goal.search_goal(
            rt,
            SearchMail(
                query="uber eats",
                goal=goal(
                    rt.request.instruction,
                    continue_previous=True,
                    entity="uber eats",
                    purpose="discovery",
                ),
            ),
        )
    with pytest.raises(mail_goal.MailRepair, match="USER goal"):
        mail_goal.search_goal(
            rt,
            SearchMail(
                query="",
                query_terms=["invented"],
                goal=goal(rt.request.instruction, continue_previous=True, entity="uber eats"),
            ),
        )
    with pytest.raises(mail_goal.MailRepair, match="typed goal"):
        mail_goal.search_goal(rt, SearchMail(query="uber eats"))


def test_snippet_removes_outlook_metadata_and_padding_not_content():
    body = (
        "<!--[if mso]><xml><o:PixelsPerInch>96</o:PixelsPerInch></xml><![endif]-->"
        "<p>96 apples &amp; Café 👩‍💻 می‌خواهم</p>"
    )
    assert snippet(strip_html(body) + " &#847; " * 100) == "96 apples & Café 👩‍💻 می‌خواهم"
    assert snippet("a\u034fb") == "a\u034fb"
    assert not has_visible_text("\u034f")
    assert has_visible_text("96")


async def test_reply_failure_cannot_substitute_prose_and_has_bounded_retry(
    mailbox, db_sessionmaker
):
    turn, _ = await sender_lookup(db_sessionmaker)
    text = "help put together a response to his latest email"
    turn, result = await advance(
        db_sessionmaker,
        turn,
        text,
        reply(text, target="latest_inbound"),
        *[
            tool("respond", kind="message", text="Here is the response I drafted.")
            for _ in range(7)
        ],
    )
    assert len(result["trace"]) == 8
    assert result["error_code"] == "mail_reply_not_prepared"
    assert all(t.get("reason") == "reply_preparation_incomplete" for t in result["trace"][1:])
    assert "This chat turn" in result["text"]
    text = "try again"
    _, result = await advance(
        db_sessionmaker,
        turn,
        text,
        reply(text, reference="mail-2", continue_previous=True, target="latest_inbound"),
        tool("read_email", reference="mail-1"),
        reply(text, continue_previous=True, target="latest_inbound"),
    )
    assert result["trace"][0]["reason"] == "reply_retry_target_changed"
    assert result["kind"] == "task"


@pytest.mark.parametrize("changed", ["outbound", "sender", "newer"])
async def test_changed_target_fails_closed(mailbox, db_sessionmaker, changed):
    turn, _ = await sender_lookup(db_sessionmaker)
    if changed == "outbound":
        mailbox["rows"]["c1"]["labelIds"] = ["SENT"]
    elif changed == "sender":
        mailbox["rows"]["c1"]["payload"]["headers"][0]["value"] = "Other <other@example.test>"
    else:
        mailbox["newer_thread_message"] = True
    text = "put together a response to his latest email"
    _, result = await advance(
        db_sessionmaker,
        turn,
        text,
        tool("read_email", reference="mail-1"),
        reply(text, target="latest_inbound"),
    )
    assert result["error_code"] == "mail_reply_not_prepared"
    assert (
        result["trace"][1]["reason"]
        == {
            "outbound": "reply_target_outbound",
            "sender": "reply_sender_changed",
            "newer": "reply_target_stale",
        }[changed]
    )
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantTask)) == 0


async def test_wrong_scope_repaired_before_task_creation(mailbox, db_sessionmaker):
    turn, _ = await sender_lookup(db_sessionmaker)
    text = "put together a reply to his newest email"
    _, result = await advance(
        db_sessionmaker,
        turn,
        text,
        tool("read_email", reference="mail-1", scope="selected_message"),
        reply(text, target="latest_inbound"),
        tool("read_email", reference="mail-1", scope="thread"),
        reply(text, target="latest_inbound"),
    )
    assert result["trace"][1]["reason"] == "reply_scope_unread"
    assert result["kind"] == "task"


async def test_unchecked_search_failure_keeps_goal_hides_unverified_cards(mailbox, db_sessionmaker):
    text = "check my latest uber eats receipt"
    turn, result = await advance(
        db_sessionmaker,
        None,
        text,
        tool(
            "search_mail",
            query="uber eats",
            goal=goal(text, entity="uber eats", purpose="receipt", latest=True),
        ),
        tool("respond", kind="message", text="Your latest receipt is a promotion."),
    )
    assert result["error_code"] == "mail_search_incomplete"
    assert result["search"]["results"] == []
    assert not result["search"]["coverage"]["relevance_checked"]
    assert result["trace"][1]["reason"] == "mail_candidates_unchecked"
    async with db_sessionmaker() as db:
        state = store.decode(await db.get(Conversation, turn.conversation_id))
        assert state[mail_goal.KEY]["purpose"] == "receipt"


async def test_two_page_budget_and_uncertain_receipt_are_not_positive_facts(
    mailbox, db_sessionmaker
):
    mailbox["endless"] = True
    text = "check my latest uber eats receipt"
    assessments = [
        assessment(
            f"mail-{i}",
            "irrelevant",
            base64.urlsafe_b64decode(mailbox["rows"][f"b{i}"]["payload"]["body"]["data"]).decode(),
        )
        for i in range(1, 6)
    ]
    _, result = await advance(
        db_sessionmaker,
        None,
        text,
        tool(
            "search_mail",
            query="uber eats",
            goal=goal(text, entity="uber eats", purpose="receipt", latest=True),
        ),
        tool("read_search_results", references=[f"mail-{i}" for i in range(1, 6)]),
        tool("more_mail"),
        tool("read_search_results", references=["mail-6"]),
        tool("more_mail"),
        tool(
            "respond",
            kind="message",
            text="I cannot confirm a receipt.",
            mail_assessments=[
                *assessments,
                assessment("mail-6", "uncertain", "Total paid $12.00."),
            ],
        ),
    )
    # The engine's successful-call dedupe also forbids reusing more_mail in a turn.
    assert result["trace"][4]["status"] == "invalid"
    assert "couldn’t verify" in result["text"]
    assert result["search"]["results"] == []
    # Local relevance filters may fill each logical result page from up to five
    # provider pages; the duplicate provider token still cannot loop forever.
    assert len([r for r in mailbox["calls"] if r.url.path.endswith("/messages")]) == 6


async def test_separate_user_terms_are_and_query_not_one_phrase(mailbox, db_sessionmaker):
    text = "find my Uber receipt"
    _, result = await advance(
        db_sessionmaker,
        None,
        text,
        tool(
            "search_mail",
            query="",
            query_terms=["Uber", "receipt"],
            goal=goal(text, entity="Uber", purpose="receipt"),
        ),
        tool("read_search_results", references=["mail-1"]),
        tool(
            "respond",
            kind="message",
            text="I found an order confirmation among these checked results.",
            evidence=[{"reference": "mail-1", "quote": "Total paid $12.00."}],
            mail_assessments=[assessment("mail-1", "relevant", "Order confirmed.")],
        ),
    )
    assert len(result["search"]["results"]) == 1
    assert any('"Uber" "receipt"' in r.url.params.get("q", "") for r in mailbox["calls"])
    assert not any('"Uber receipt"' in r.url.params.get("q", "") for r in mailbox["calls"])


def test_refinement_retains_window_anchor_scope_and_sender():
    rt = runtime("find latest Uber receipt in my inbox last month from orders@example.test")
    mail_goal.search_goal(
        rt,
        SearchMail(
            query="Uber",
            folder="INBOX",
            date_phrase="last month",
            sender_email="orders@example.test",
            goal=goal(rt.request.instruction, entity="Uber", purpose="receipt", latest=True),
        ),
    )
    anchor = rt.state[mail_goal.KEY]["anchor"]
    rt.request = rt.request.model_copy(update={"instruction": "it is uber eats"})
    rt.mail_anchor += timedelta(days=31)
    refined = mail_goal.search_goal(
        rt,
        SearchMail(
            query="uber eats",
            folder="all_mail",
            date_phrase="",
            sender_email="",
            goal=goal(rt.request.instruction, entity="uber eats", continue_previous=True),
        ),
    )
    assert refined.folder == "INBOX" and refined.date_phrase == "last month"
    assert refined.sender_email == "orders@example.test"
    assert rt.state[mail_goal.KEY]["anchor"] == anchor


@pytest.mark.parametrize(
    "text", ["don't reply to that email", "do not draft a response", "never mind"]
)
def test_declined_preparation_does_not_keep_pending_reply(text):
    rt = runtime(
        text,
        {"refs": {}, "history": [], "result_order": [], mail_goal.REPLY: {"status": "preparing"}},
    )
    with pytest.raises(mail_goal.MailRepair) as exc:
        mail_goal.prepare_reply(
            rt,
            PrepareWorkflow(
                intent="reply",
                reference="mail-1",
                preparation_intent={"operation": "reply", "source": text},
            ),
        )
    assert exc.value.code == "reply_preparation_declined"
    assert mail_goal.REPLY not in rt.state


def test_route_reply_continuity_ownership_stale_version_and_replay(
    mailbox, db_client, auth_headers, monkeypatch
):
    text = "check the email from Alex Chen"
    first = request().model_copy(update={"instruction": text})

    def post(turn, decisions, owner=1):
        model = Model(*decisions)
        monkeypatch.setattr(engine, "ConversationModel", lambda: model)
        return db_client.post(
            "/assistant/conversation-turns", headers=auth_headers(owner), json=turn.model_dump()
        )

    result = post(
        first,
        [
            tool("search_mail", query="Alex Chen", goal=goal(text, sender_name="Alex Chen")),
            tool(
                "respond", kind="clarification", text="What would you like to do with that email?"
            ),
        ],
    )
    assert result.status_code == 200, result.text
    second = request().model_copy(
        update={
            "instruction": "prepare an answer to his latest email",
            "conversation_id": first.conversation_id,
            "expected_version": 1,
        }
    )
    decisions = [
        tool("read_email", reference="mail-1"),
        reply(second.instruction, target="latest_inbound"),
    ]
    before = len(mailbox["calls"])
    assert post(second, decisions, owner=2).status_code == 404
    assert len(mailbox["calls"]) == before
    assert post(second.model_copy(update={"expected_version": 0}), decisions).status_code == 409
    response = post(second, decisions)
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "task"
    replay = post(second, [])
    assert replay.status_code == 200
    assert replay.json()["task_id"] == response.json()["task_id"]


async def test_retry_of_prepared_reply_reuses_owned_task(mailbox, db_sessionmaker):
    turn, _ = await sender_lookup(db_sessionmaker)
    text = "put together an answer to his latest email"
    turn, first = await advance(
        db_sessionmaker,
        turn,
        text,
        tool("read_email", reference="mail-1"),
        reply(text, target="latest_inbound"),
    )
    _, second = await advance(
        db_sessionmaker,
        turn,
        "try again",
        reply("try again", continue_previous=True, target="latest_inbound"),
    )
    assert first["task_id"] == second["task_id"]
    async with db_sessionmaker() as db:
        assert await db.scalar(select(func.count()).select_from(AssistantTask)) == 1


def test_assessment_cannot_borrow_receipt_from_another_thread_message():
    rt = runtime(
        "find my Uber receipt",
        {
            "history": [],
            "refs": {"mail-1": {"message_id": "a1"}},
            "result_order": ["mail-1"],
            mail_goal.KEY: {"purpose": "receipt"},
        },
    )
    rt.evidence["mail-1"] = "New promotion. Old receipt: total paid $12.00."
    rt.loaded["mail-1"] = {
        "messages": [
            {"gmail_msg_id": "a1", "body_clean": "New promotion."},
            {"gmail_msg_id": "a2", "body_clean": "Old receipt: total paid $12.00."},
        ]
    }
    from app.schemas.mail_goal import MailAssessment

    with pytest.raises(mail_goal.MailRepair) as exc:
        mail_goal.checked_assessments(
            rt,
            [
                MailAssessment(
                    reference="mail-1",
                    disposition="relevant",
                    quote="Old receipt: total paid $12.00.",
                )
            ],
        )
    assert exc.value.code == "mail_assessment_wrong_message"


def test_mixed_invisible_preheader_padding_leaves_numbers_and_joined_words():
    assert snippet("&#847;&zwnj;" * 200 + " 96 items 👩‍💻 می‌خواهم") == "96 items 👩‍💻 می‌خواهم"
    assert snippet("a\u034fb") == "a\u034fb"


async def test_legacy_cursor_remains_valid_but_changed_new_filter_is_fenced(mailbox):
    from app.assistant import inbox_chat
    from app.assistant.summary import digest
    from app.mail.search import encode_cursor
    from app.schemas.inbox_chat import InboxFilters

    now = datetime.now(UTC)
    filters = InboxFilters(
        schema_version="1.0",
        query="uber eats",
        received_from=now - timedelta(days=365),
        received_before=now,
        limit=5,
    )
    old_fields = filters.model_dump(
        mode="json", exclude={"query_terms", "sender_name", "inbound_only"}
    )
    old_scope = digest({"release": inbox_chat.RELEASE, **old_fields, "page_size": 5})
    _, version, _ = await live.account(1)
    cursor = encode_cursor(1, version, old_scope, "page-two")
    page = await inbox_chat.search(1, filters, cursor)
    assert [r["message_id"] for r in page["results"]] == ["b6"]
    before = len(mailbox["calls"])
    with pytest.raises(ApiError) as exc:
        await inbox_chat.search(1, filters.model_copy(update={"inbound_only": True}), cursor)
    assert exc.value.code == "mail_search_cursor_invalid"
    assert len(mailbox["calls"]) == before


def test_reply_content_about_cancellation_is_not_a_cancel_command():
    text = "draft a response asking him to cancel the subscription"
    rt = runtime(text)
    with pytest.raises(mail_goal.MailRepair) as exc:
        mail_goal.prepare_reply(
            rt,
            PrepareWorkflow(
                intent="reply",
                reference="mail-1",
                preparation_intent={"operation": "reply", "source": text},
            ),
        )
    assert exc.value.code == "reply_source_unread"


def test_active_mail_goal_requires_the_typed_reply_contract():
    rt = runtime(
        "draft a reply to his latest email",
        {"history": [], "refs": {}, "result_order": [], mail_goal.KEY: {"purpose": "discovery"}},
    )
    with pytest.raises(mail_goal.MailRepair) as exc:
        mail_goal.prepare_reply(rt, PrepareWorkflow(intent="reply", reference="mail-1"))
    assert exc.value.code == "reply_intent_required"


async def test_user_resolves_sender_ambiguity_without_losing_reply_request(
    mailbox, db_sessionmaker
):
    mailbox["ambiguous"] = True
    turn, _ = await sender_lookup(db_sessionmaker)
    text = "put together a response to the latest email he sent"
    turn, result = await advance(
        db_sessionmaker,
        turn,
        text,
        tool("read_email", reference="mail-1"),
        reply(text, target="latest_inbound"),
        tool("respond", kind="clarification", text="Which Alex Chen do you mean?"),
    )
    assert result["kind"] == "clarification"
    choice = "the first one from Acme"
    _, result = await advance(
        db_sessionmaker,
        turn,
        choice,
        tool("read_email", reference="mail-1"),
        tool(
            "prepare_workflow",
            intent="reply",
            reference="mail-1",
            preparation_intent={
                "operation": "resolve_target",
                "source": choice,
                "continue_previous": True,
                "target": "reference",
            },
        ),
    )
    assert result["kind"] == "task"
    async with db_sessionmaker() as db:
        task = await db.get(AssistantTask, result["task_id"])
        assert text in task.instruction and choice in task.instruction
        assert task.draft_input["reply_message_id"] == "c1"
