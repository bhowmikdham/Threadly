"""Bounded, reference-only mail goals and evidence-backed result selection.

Semantic decisions are proposals from USER dialogue. They can prepare a reviewable
reply but cannot save/send mail, approve an action, or supply recipient addresses.
"""

import re

from app.mail.presentation import has_visible_text

KEY = "mail_goal"
REPLY = "mail_reply_goal"
MAX_PAGES = 2
DECLINED = re.compile(
    r"^\s*(?:please\s+)?(?:(?:don't|do not|never)\s+(?:draft|reply|respond|write|prepare)\b|"
    r"(?:cancel(?:\s+(?:it|that|this|the (?:reply|draft|request)))?|never mind)\s*[.!]?\s*$)",
    re.I,
)


class MailRepair(ValueError):
    def __init__(self, code, message, references=()):
        self.code, self.message, self.references = code, message, list(references)
        super().__init__(message)


def exact_source(runtime, source):
    if source.strip() != runtime.request.instruction.strip():
        raise MailRepair(
            "mail_intent_source_mismatch",
            "Copy the complete current USER turn into source; "
            "do not quote email or assistant text.",
        )


def literal(value, source):
    return not value or value.casefold() in source.casefold()


def begin_turn(runtime):
    state = getattr(runtime, "state", {})
    request = getattr(runtime, "request", None)
    if (
        state.get(REPLY)
        and request
        and DECLINED.search(request.instruction)
        and state.get("active_goal") in {None, "reply", "email_draft"}
    ):
        from app.conversation import goals

        goals.close(state, state.get(REPLY))
        state.pop(REPLY, None)


def search_goal(runtime, args):
    intent = args.goal
    if intent is None:
        if runtime.state.get(KEY) or re.search(
            r"\b(?:receipts?|applications?)\b", runtime.request.instruction, re.I
        ):
            raise MailRepair(
                "mail_goal_required",
                "Use a typed goal: retain receipt/application and latest constraints on "
                "refinement, or start a new goal explicitly. Copy its entity from USER text.",
            )
        # Old callers remain valid; a fresh search must not retain an unrelated
        # typed goal. Refinements use the explicit continuation contract.
        runtime.state.pop(KEY, None)
        runtime.state.pop(REPLY, None)
        return args
    exact_source(runtime, intent.source)
    previous = runtime.state.get(KEY)
    if intent.continue_previous and not previous:
        raise MailRepair("mail_goal_missing", "Start this mail goal with continue_previous=false.")
    goal = (
        dict(previous)
        if intent.continue_previous
        else {
            "instruction": runtime.request.instruction.strip(),
            "entity": "",
            "sender_name": "",
            "purpose": "discovery",
            "latest": False,
            "anchor": runtime.mail_anchor.isoformat(),
            "timezone": runtime.request.timezone,
            "date_phrase": args.date_phrase,
            "folder": args.folder,
            "inbox_category": args.inbox_category,
            "sender_email": args.sender_email,
        }
    )
    for field in ("entity", "sender_name"):
        value = getattr(intent, field).strip()
        if value and value != goal[field]:
            if not literal(value, runtime.request.instruction):
                raise MailRepair(
                    "mail_goal_literal_invalid", "Copy changed search fields from USER text."
                )
            goal[field] = value
    if intent.continue_previous:
        # Refining a merchant/person does not silently replace receipt/status
        # or latest intent. A different task starts a new goal explicitly.
        if intent.purpose is not None and intent.purpose != goal["purpose"]:
            raise MailRepair("mail_goal_changed", "Keep the pending purpose or start a new goal.")
        if intent.latest is not None and intent.latest != goal["latest"]:
            raise MailRepair("mail_goal_changed", "Keep the pending ordering or start a new goal.")
        if runtime.request.instruction.strip() not in goal["instruction"].split(
            "\nUser follow-up: "
        ):
            goal["instruction"] += "\nUser follow-up: " + runtime.request.instruction.strip()
        # Ordinary tool defaults are not evidence that the USER widened the
        # scope. Changing scope starts a new explicit goal (and date anchor).
    else:
        goal["purpose"] = intent.purpose or "discovery"
        goal["latest"] = bool(intent.latest)
    if len(goal["instruction"]) > 8000:
        raise MailRepair("mail_goal_limit", "Start a new concise mail goal.")
    if goal["purpose"] != "discovery" and not goal["entity"]:
        raise MailRepair(
            "mail_entity_required", "Copy the user-supplied company/merchant into entity."
        )
    terms = args.query_terms
    if len(set(terms)) != len(terms) or any(
        not term.strip() or len(term) > 200 or not literal(term, goal["instruction"])
        for term in terms
    ):
        raise MailRepair(
            "mail_search_terms_invalid", "Use only distinct search terms from this USER goal."
        )
    # Entity discovery is intentionally wider than the task label. Separate
    # terms are an explicit AND refinement, never an accidental exact phrase.
    query = "" if terms else goal["entity"] or goal["sender_name"] or args.query
    goal.update(
        assessments={},
        pages_read=0,
        request_id=runtime.request.request_id,
        search_complete=False,
        sender_identity_count=0,
    )
    runtime.state[KEY] = goal
    if not intent.continue_previous:
        runtime.state.pop(REPLY, None)
    return args.model_copy(
        update={
            "query": query,
            **(
                {"selection": "recent_matches", "limit": 5}
                if goal["purpose"] != "discovery"
                else {}
            ),
            **{
                key: goal[key]
                for key in ("date_phrase", "folder", "inbox_category", "sender_email")
            },
        }
    )


def checked_assessments(runtime, assessments):
    goal = runtime.state.get(KEY)
    if not goal or goal["purpose"] == "discovery":
        if assessments:
            raise MailRepair(
                "mail_goal_missing", "Assess results only for the active receipt/application goal."
            )
        return
    staged = dict(goal.get("assessments", {}))
    seen = set()
    for item in assessments:
        if item.reference in seen or item.reference not in runtime.state.get("result_order", []):
            raise MailRepair(
                "mail_assessment_reference_invalid", "Use each current search reference once."
            )
        seen.add(item.reference)
        evidence = runtime.evidence.get(item.reference, "")
        if not has_visible_text(item.quote) or " ".join(item.quote.split()) not in " ".join(
            evidence.split()
        ):
            raise MailRepair(
                "mail_assessment_unverified",
                "Read this candidate and quote its actual content.",
                [item.reference],
            )
        source = runtime.state["refs"][item.reference]
        selected = next(
            (
                m
                for m in runtime.loaded.get(item.reference, {}).get("messages", [])
                if m["gmail_msg_id"] == source.get("message_id")
            ),
            {},
        )
        selected_text = " ".join(
            str(selected.get(k) or "") for k in ("subject", "from_addr", "body_clean")
        )
        if " ".join(item.quote.split()) not in " ".join(selected_text.split()):
            raise MailRepair(
                "mail_assessment_wrong_message",
                "Assess this candidate's own content, not another message in its thread.",
                [item.reference],
            )
        staged[item.reference] = item.disposition
    goal["assessments"] = staged


def finish_search(runtime, answer):
    """Validate before filtering; hiding a card cannot hide a newer unread hit."""
    goal = getattr(runtime, "state", {}).get(KEY)
    page = getattr(runtime, "search_page", None)
    if not goal or goal["purpose"] == "discovery" or not page:
        return None
    checked_assessments(runtime, answer.mail_assessments)
    assessed = goal.get("assessments", {})
    rows = page.get("results", [])
    missing = [r["reference"] for r in rows if r["reference"] not in assessed]
    if missing:
        raise MailRepair(
            "mail_candidates_unchecked",
            "Read the candidates and assess relevance to the retained goal before answering.",
            missing,
        )
    relevant = {ref for ref, status in assessed.items() if status == "relevant"}
    if not relevant and page.get("next_cursor") and goal.get("pages_read", 0) < MAX_PAGES:
        raise MailRepair(
            "mail_more_required",
            "No requested item is verified yet. Use more_mail and inspect the next bounded page; "
            "retain the goal.",
        )
    if relevant and (
        not answer.evidence or any(e.reference not in relevant for e in answer.evidence)
    ):
        raise MailRepair(
            "mail_answer_mismatch",
            "Cite the verified relevant messages, not excluded promotions or unrelated candidates.",
        )
    if relevant:
        return None
    purpose = "a receipt" if goal["purpose"] == "receipt" else "an application outcome"
    return {
        "kind": "message",
        "text": f"I couldn’t verify {purpose} in the results I checked within this date range. "
        "This does not establish that no such email exists. "
        "You can narrow the sender or dates to continue.",
        "evidence": [],
    }


def align_cards(runtime):
    goal = getattr(runtime, "state", {}).get(KEY)
    page = getattr(runtime, "search_page", None)
    if not goal or goal["purpose"] == "discovery" or not page:
        return
    rows = page.get("results", [])
    keep = {ref for ref, status in goal.get("assessments", {}).items() if status == "relevant"}
    runtime.search_page = {
        **page,
        "results": [r for r in rows if r["reference"] in keep],
        "coverage": {
            **page["coverage"],
            "relevance_checked": all(r["reference"] in goal.get("assessments", {}) for r in rows),
            "excluded_candidates": sum(r["reference"] not in keep for r in rows),
        },
    }
    runtime.state["result_order"] = [ref for ref in runtime.state["result_order"] if ref in keep]


def prepare_reply(runtime, args):
    intent = args.preparation_intent
    if intent is None:
        if args.intent == "reply" and not args.compound and runtime.state.get(KEY):
            raise MailRepair(
                "reply_intent_required",
                "Use preparation_intent to retain this mail goal's USER directive and target.",
            )
        return None
    exact_source(runtime, intent.source)
    if args.intent != "reply" or args.compound:
        raise MailRepair(
            "reply_preparation_only",
            "Typed preparation applies only to a single source-bound reply draft.",
        )
    if DECLINED.search(intent.source):
        runtime.state.pop(REPLY, None)
        raise MailRepair(
            "reply_preparation_declined",
            "The USER declined preparation; acknowledge without creating a task.",
        )
    previous = runtime.state.get(REPLY)
    if intent.continue_previous and not previous:
        raise MailRepair(
            "reply_goal_missing", "Use the current USER request with continue_previous=false."
        )
    resolving = intent.operation == "resolve_target"
    if resolving and (
        not intent.continue_previous or not previous or previous.get("status") != "preparing"
    ):
        raise MailRepair(
            "reply_resolution_without_goal", "Resolve a target only for a pending reply request."
        )
    source = runtime.state["refs"].get(args.reference, {})
    identity = {key: source.get(key) for key in ("message_id", "thread_id")}
    if (
        intent.continue_previous
        and not resolving
        and (
            args.reference != previous["reference"]
            or intent.target != previous["target"]
            or identity != previous.get("source_identity", identity)
        )
    ):
        raise MailRepair(
            "reply_retry_target_changed",
            "Keep the retained reply target on retry; selecting another email needs a new request.",
        )
    if intent.continue_previous and previous.get("status") == "prepared":
        runtime.reuse_mail_reply_task = previous["task_id"]
        return previous["instruction"]
    instruction = (
        previous["instruction"] if intent.continue_previous else runtime.request.instruction.strip()
    )
    if intent.continue_previous:
        instruction += "\nUser follow-up: " + runtime.request.instruction.strip()
    goal = runtime.state.get(KEY)
    if not intent.continue_previous and goal:
        instruction = goal["instruction"] + "\nUser follow-up: " + instruction
    if len(instruction) > 8000:
        raise MailRepair("reply_goal_limit", "Start a concise new reply request.")
    runtime.mail_reply_attempted = True
    runtime.state[REPLY] = {
        **(
            {"goal_id": previous["goal_id"]}
            if intent.continue_previous and previous and previous.get("goal_id")
            else {}
        ),
        "instruction": instruction,
        "reference": args.reference,
        "target": intent.target,
        "status": "preparing",
        "source_identity": identity,
    }
    if not args.reference or args.reference not in runtime.loaded:
        raise MailRepair(
            "reply_source_unread",
            "Read the selected target and requested scope before preparing the reply.",
            [args.reference] if args.reference else [],
        )
    if args.source_scope not in runtime.read_scopes.get(args.reference, set()):
        raise MailRepair(
            "reply_scope_unread",
            "Read the requested source scope before preparing this reply.",
            [args.reference],
        )
    message = next(
        (
            m
            for m in runtime.loaded[args.reference]["messages"]
            if m["gmail_msg_id"] == source.get("message_id")
        ),
        None,
    )
    if message is None:
        raise MailRepair(
            "reply_target_missing", "Choose one available message as the reply target."
        )
    if intent.target == "latest_inbound":
        if not goal or not goal.get("search_complete"):
            raise MailRepair(
                "reply_search_unverified",
                "Complete the sender search before selecting its latest incoming result.",
            )
        if goal and goal.get("sender_identity_count", 0) > 1:
            raise MailRepair(
                "reply_sender_ambiguous",
                "More than one sender matches this name. "
                "Ask which sender the USER means before choosing a reply target.",
            )
        if message.get("is_from_user") or "SENT" in message.get("reply_metadata", {}).get(
            "label_ids", []
        ):
            raise MailRepair(
                "reply_target_outbound", "Select an incoming message, not the user's sent reply."
            )
        order = runtime.state.get("result_order", [])
        target = next(
            (
                ref
                for ref in order
                if runtime.state["refs"][ref].get("message_id") == source.get("message_id")
            ),
            None,
        )
        if not target or order.index(target) != 0:
            raise MailRepair(
                "reply_target_ambiguous",
                "Search the requested sender's incoming mail and select the newest returned "
                "candidate. Do not guess a pronoun or use an older candidate.",
            )
        if not goal or not goal.get("sender_name"):
            raise MailRepair(
                "reply_sender_unresolved",
                "Resolve the sender from USER dialogue in a sender-scoped search first.",
            )
        from app.assistant.inbox_chat import sender_matches

        if not sender_matches(message.get("from_addr"), goal["sender_name"]):
            raise MailRepair(
                "reply_sender_changed",
                "The incoming sender no longer matches the retained goal. Search again or clarify.",
            )
        received = message.get("received_at")
        if received and any(
            m.get("received_at")
            and m["received_at"] > received
            and not m.get("is_from_user")
            and "SENT" not in m.get("reply_metadata", {}).get("label_ids", [])
            and sender_matches(m.get("from_addr"), goal["sender_name"])
            for m in runtime.loaded[args.reference]["messages"]
        ):
            raise MailRepair(
                "reply_target_stale",
                "The fresh thread contains a newer incoming message from this sender. "
                "Search again and read the new target before preparing the reply.",
            )
    return instruction


def require_prepared_reply(runtime, answer):
    if incomplete(runtime) and answer.kind != "clarification":
        raise MailRepair(
            "reply_preparation_incomplete",
            "Repair the pending reply preparation; do not substitute a prose draft or completion "
            "claim. Ask a clarification only if the USER needs to resolve the target or content.",
        )


def incomplete(runtime):
    goal = getattr(runtime, "state", {}).get(REPLY)
    if (
        getattr(runtime, "mail_reply_attempted", False)
        and goal
        and goal.get("status") == "preparing"
    ):
        return {
            "kind": "message",
            "error_code": "mail_reply_not_prepared",
            "text": "I couldn’t finish preparing that reply. I’ve kept your request and source "
            "references; ask me to try again. This chat turn did not save anything in Gmail "
            "or send an email.",
            "evidence": [],
        }
    return None


def search_incomplete(runtime):
    goal = getattr(runtime, "state", {}).get(KEY)
    if goal and goal["purpose"] != "discovery" and getattr(runtime, "search_page", None):
        align_cards(runtime)
        return {
            "kind": "message",
            "error_code": "mail_search_incomplete",
            "text": "I couldn’t finish verifying the requested email in this bounded search. "
            "I’ve kept your search goal. Any cards shown are candidates already checked as "
            "relevant; this does not establish that other matching emails do not exist.",
            "evidence": [],
        }
    return None
