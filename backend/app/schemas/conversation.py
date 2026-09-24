"""Conversation transport and bounded model tools. Provider IDs are never tool arguments."""

from typing import Annotated, Literal

from pydantic import Field

from app.schemas.assistant import StrictModel
from app.schemas.continuation import ClarificationAnswer
from app.schemas.inbox_chat import InboxChatRequest


class ConversationTurn(InboxChatRequest):
    conversation_id: str = Field(
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    )
    request_id: str = Field(
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
    )
    expected_version: int = Field(ge=0)
    # Omission keeps the pinned source; explicit null clears it.
    context_snapshot_id: str | None = Field(default=None, max_length=36)
    active_task_id: str | None = Field(default=None, max_length=36)


class SearchMail(StrictModel):
    query: str = Field(max_length=200)
    date_phrase: str = Field(default="", max_length=100)
    folder: Literal["all_mail", "INBOX", "SENT"] = "all_mail"


class MoreMail(StrictModel):
    pass


class ReadEmail(StrictModel):
    reference: str = Field(min_length=1, max_length=40)
    scope: Literal["selected_message", "visible_thread"] = "selected_message"


class ReadSearchResults(StrictModel):
    references: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        min_length=1, max_length=5
    )


class Evidence(StrictModel):
    reference: str = Field(min_length=1, max_length=40)
    quote: str = Field(min_length=1, max_length=500)


class Respond(StrictModel):
    kind: Literal["message", "recommendation", "clarification"]
    text: str = Field(min_length=1, max_length=4000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=5)


class PrepareWorkflow(StrictModel):
    intent: Literal["summarise", "reply", "compose", "plan_schedule", "other"]
    reference: str | None = Field(default=None, max_length=40)
    compound: bool = False
    source_scope: Literal["selected_message", "visible_thread"] = "selected_message"
    to_refs: list[str] = Field(default_factory=list, max_length=20)
    cc_refs: list[str] = Field(default_factory=list, max_length=20)
    bcc_refs: list[str] = Field(default_factory=list, max_length=20)


class AnswerQuestion(StrictModel):
    answer: ClarificationAnswer


class ReviseDraft(StrictModel):
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=20000)


TOOLS = {
    "search_mail": (
        SearchMail,
        (
            "Find email on demand using a user-supplied literal. The query is "
            "quoted as one exact Gmail phrase: for 'latest GYG order', pass only "
            "the merchant name 'GYG', not 'GYG order' or task words. Returns "
            "five recent matches with references, not necessarily five actual "
            "orders. Default coverage is the past year. No mailbox import."
        ),
    ),
    "more_mail": (
        MoreMail,
        "Read the next page of the last search. Keep its filters and result ordering.",
    ),
    "read_email": (
        ReadEmail,
        (
            "Read a selected source or a returned mail reference before answering "
            "about it or preparing a workflow. A selected_message read returns one "
            "selected or searched email; visible_thread reads the owned pinned capture. "
            "Only backend-issued references are valid."
        ),
    ),
    "read_search_results": (
        ReadSearchResults,
        (
            "Read one to five backend-issued mail-N references from the current search. "
            "Returns bounded excerpts of each selected email so you can distinguish "
            "relevant messages from promotions and cite exact returned text. "
            "Cannot read the pinned selection or widen results to a thread."
        ),
    ),
    "prepare_workflow": (
        PrepareWorkflow,
        (
            "Prepare a summary, draft, plan or scheduling proposal using existing "
            "workflows. The default selected_message source_scope binds one email; "
            "visible_thread is only for an explicitly requested workflow over the "
            "owned pinned capture and requires a matching read. Use compound=true "
            "for multiple dependent operations. Never "
            "sends mail or books events. Terminal for this turn; execution status "
            "arrives later."
        ),
    ),
    "answer_question": (
        AnswerQuestion,
        (
            "Answer the active workflow's typed pending question with user-supplied "
            "information. Never approve an action. Terminal for this turn."
        ),
    ),
    "revise_draft": (
        ReviseDraft,
        (
            "Revise the CURRENT saved draft wording, preserving its facts, user "
            "edits, recipients and subject for replies. Produces an unreviewed "
            "revision and invalidates old approval. Terminal for this turn."
        ),
    ),
    "respond": (
        Respond,
        (
            "Finish with a concise helpful answer, grounded recommendation or "
            "material clarification. Cite exact quotes from read_email or "
            "read_search_results for email claims. Search-card snippets alone "
            "are not citation evidence. Greeting and conversational answers "
            "need no evidence. A "
            "no-action recommendation is success."
        ),
    ),
}


def tool_config():
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": name,
                    "description": description,
                    "inputSchema": {"json": schema.model_json_schema()},
                }
            }
            for name, (schema, description) in TOOLS.items()
        ]
    }
