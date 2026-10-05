"""Bounded Calendar request memory. Retain user intent, never provider evidence."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.api.errors import ApiError
from app.calendar import conversation_tools, day_availability, service

KEY = "calendar_read_request"
WINDOW_TOOLS = frozenset(
    {
        "check_day_availability",
        "check_time_availability",
        "search_calendar_events",
        "find_busy_times",
        "find_free_times",
        "find_overlapping_events",
    }
)


def model_context(state):
    previous = state.get(KEY)
    if not previous:
        return None
    return {
        "tool": previous["tool"],
        "user_instruction": previous["instruction"],
        "date_anchor": previous["date_anchor"],
        "needs_interpretation": previous.get("arguments") is None,
        "note": "Request context only. Repeat the read for fresh facts and current selections.",
    }


def legacy_context(state, created_at):
    """Recover old Calendar intent without interpreting assistant/provider prose as a date."""
    if not state.get("calendar_read_anchor"):
        return None
    receipts = {r["request_id"]: r["response"] for r in state.get("receipts", [])}
    for entry in reversed(state.get("history", [])):
        response = receipts.get(entry.get("request_id"), {})
        trace = response.get("trace", [])
        tool = trace[-1].get("tool") if trace else None
        if entry.get("source") == "calendar_availability":
            tool = "check_day_availability"
        if entry.get("source") in {"calendar_availability", "calendar_tools"} or (
            entry.get("kind") == "message"
            and str(response.get("error_code", "")).startswith("calendar_")
        ):
            if tool not in WINDOW_TOOLS:
                return None
            return {
                "tool": tool,
                "instruction": entry["user"],
                "arguments": None,
                "date_anchor": state["calendar_read_anchor"],
                # The old format has no per-turn date anchor. Only resume relative
                # dates if the entire possible interval is one local calendar day.
                "earliest_anchor": created_at.isoformat(),
                "last_request_id": entry.get("request_id"),
            }
        if entry.get("kind") != "clarification" or tool not in WINDOW_TOOLS:
            break
    return None


async def retry(runtime):
    previous = runtime.state.get(KEY)
    if not previous:
        return {"kind": "clarification", "text": "What would you like me to check again?"}
    # A retry cannot drop a newly supplied date/time or external-action constraint.
    # Its semantic selection comes from the coordinator, not a retry keyword router.
    if re.search(
        r"\b(?:don't|do not|stop|cancel|never mind|nevermind)\b",
        runtime.request.instruction,
        re.I,
    ):
        return {"kind": "message", "text": "Okay, I won't repeat the Calendar check."}
    from types import SimpleNamespace

    conversation_tools.validate_scope(SimpleNamespace(date_source=""), runtime.request.instruction)
    previous["last_request_id"] = runtime.request.request_id
    if previous.get("arguments") is None:
        runtime.calendar_reparse = previous
        raise ApiError(
            422,
            "calendar_retry_interpretation_required",
            "This older conversation retained the original USER request but not its typed "
            f"arguments. Reissue {previous['tool']} by interpreting the user_instruction in "
            "previous_calendar_request, quoting its original date words. The backend will "
            "bind those arguments to that request and verify its date anchor. Do not ask "
            "the user to repeat the date before attempting this interpretation.",
        )
    from app.schemas.conversation import TOOLS

    args = TOOLS[previous["tool"]][0].model_validate(previous["arguments"])
    return await read(runtime, previous["tool"], args, previous=previous)


async def read(runtime, name, args, *, previous=None):
    previous = previous or runtime.calendar_reparse
    remembered = runtime.state.get(KEY)
    if not previous and remembered and name == remembered["tool"] and args.subject != "other":
        # Models may quote the earlier user's date instead of choosing the explicit
        # retry tool. Accept that provenance only when the latest turn adds no new
        # constraints; replay the stored interpretation rather than guess its date.
        try:
            conversation_tools.validate_scope(args, runtime.authoritative_instruction())
        except ValueError:
            try:
                conversation_tools.validate_scope(args, remembered["instruction"])
            except ValueError:
                pass
            else:
                return await retry(runtime)
    instruction = previous["instruction"] if previous else runtime.authoritative_instruction()
    date_anchor = datetime.fromisoformat(previous["date_anchor"]) if previous else None
    if previous and name != previous["tool"]:
        raise ValueError("Repeat the original Calendar operation without changing its scope")
    explicit_date = args.date is not None and args.date.kind == "absolute"
    if explicit_date:
        try:
            conversation_tools.literal(args.date.start, instruction)
            if args.date.end:
                conversation_tools.literal(args.date.end, instruction)
        except ValueError:
            explicit_date = False
    if previous and previous.get("earliest_anchor") and not explicit_date:
        pref = await service.get_preferences(runtime.owner)
        zone = ZoneInfo(pref.preferences.timezone)
        lower = datetime.fromisoformat(previous["earliest_anchor"])
        if lower.astimezone(zone).date() != date_anchor.astimezone(zone).date():
            return {
                "kind": "clarification",
                "text": "Which date should I recheck? This older conversation did not save "
                "the original date, so I don't want to check the wrong day.",
            }
    valid_scope = args.subject != "other"
    try:
        conversation_tools.validate_scope(args, instruction)
    except ValueError:
        valid_scope = False
    options = {"anchor": runtime.calendar_anchor}
    if date_anchor is not None:
        options["date_anchor"] = date_anchor
    if name == "check_day_availability":
        result = await day_availability.answer(runtime.owner, instruction, window=args, **options)
    else:
        result = await conversation_tools.execute(runtime.owner, name, args, instruction, **options)
    if valid_scope and result["kind"] == "message":
        arguments = args.model_dump(mode="json")
        # Pin the resolved civil day, not the relative word "tomorrow". A later
        # retry (including a timezone preference change) still means this date.
        resolved_day = result.get("calendar_availability", {}).get("date") or result.get(
            "calendar_tools", {}
        ).get("date")
        if resolved_day:
            arguments.update(date={"kind": "absolute", "start": resolved_day}, date_phrase="")
            arguments["date_source"] = args.date_source or args.date_phrase
            arguments["subject"] = args.subject or "self"
        runtime.state[KEY] = {
            "tool": name,
            "instruction": instruction,
            "arguments": arguments,
            "date_anchor": (date_anchor or runtime.calendar_anchor).isoformat(),
            "last_request_id": getattr(runtime.request, "request_id", None),
        }
    return result
