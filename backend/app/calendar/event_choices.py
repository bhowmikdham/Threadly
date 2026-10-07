"""Owned, expiring Calendar destination choices; labels never become event instructions."""

import re
from datetime import UTC, datetime
from uuid import uuid4

from app.calendar import service


def pending(state):
    value = state.get("calendar_event_request")
    return (
        value if value and datetime.fromisoformat(value["expires_at"]) > datetime.now(UTC) else None
    )


def public(state):
    value = pending(state)
    options = value.get("calendar_choices") if value and not value.get("action_id") else None
    if not options:
        return None
    return {
        "choices": [
            {"choice_id": row["choice_id"], "label": row["label"], "access": "editable"}
            for row in options["options"]
        ],
        "expires_at": value["expires_at"],
    }


def model_context(state):
    value = pending(state)
    if not value:
        return None
    result = {
        k: v
        for k, v in value.items()
        if k
        not in {
            "calendar_choices",
            "selected_calendar",
            "previous_calendar_names",
            "creation_origin",
        }
    }
    result["calendar_choices"] = public(state)
    if selected := value.get("selected_calendar"):
        result["selected_calendar_label"] = selected["label"]
    return result


def eligible(rows, preferences):
    return [
        row for row in rows if row["event_write_acl"] and row["id"] in preferences["calendar_ids"]
    ]


def offer(state, rows, preference_version, account_version, *, error=None):
    value = pending(state)
    if not value:
        return unavailable()
    value.pop("selected_calendar", None)
    value["calendar_choices"] = {
        "preference_version": preference_version,
        "account_version": account_version,
        "options": [
            {
                "choice_id": str(uuid4()),
                "calendar_id": row["id"],
                "label": row["summary"] or "Untitled calendar",
            }
            for row in rows[:10]
        ],
    }
    text = (
        "Which calendar should I use?"
        if rows
        else "Choose a calendar you can edit in Calendar settings, then try again."
    )
    if error:
        text = "The calendar choices changed. Please choose again." if rows else text
    return {
        "kind": "clarification",
        "text": text,
        "calendar_choices": public(state),
        **({"error_code": error} if error else {}),
    }


def unavailable():
    return {
        "kind": "clarification",
        "error_code": "calendar_choice_unavailable",
        "text": "That calendar choice is no longer available. Please repeat the event details.",
    }


def explicit_selection(name, text):
    return bool(
        re.fullmatch(
            r"\s*(?:(?:please\s+)?(?:use|choose|select|pick)\s+(?:the\s+)?(?:calendar\s+)?)?"
            + re.escape(name)
            + r"[.! ]*",
            text,
            re.I,
        )
    )


def ordinal(value):
    words = [
        "first",
        "second",
        "third",
        "fourth",
        "fifth",
        "sixth",
        "seventh",
        "eighth",
        "ninth",
        "tenth",
    ]
    match = re.fullmatch(
        r"(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?|" + "|".join(words) + r")(?:\s+(?:one|calendar))?",
        value.strip().casefold(),
    )
    if not match:
        return None
    return words.index(match[1]) + 1 if match[1] in words else int(re.match(r"\d+", match[1])[0])


def resolve(state, rows, preference_version, account_version, name=""):
    value = pending(state)
    if value is None:
        return None, unavailable()
    saved = value.get("calendar_choices", {})
    selected = value.get("selected_calendar")
    index = ordinal(name) if name else None
    if index is not None:
        options = saved.get("options", [])
        selected = options[index - 1] if 1 <= index <= len(options) else None
        if selected:
            selected = {
                **selected,
                "preference_version": saved["preference_version"],
                "account_version": saved["account_version"],
            }
        else:
            return None, offer(
                state, rows, preference_version, account_version, error="calendar_choices_changed"
            )
    elif name:
        matches = [
            row
            for row in rows
            if name.casefold() in {row["summary"].casefold(), row["id"].casefold()}
        ]
        if len(matches) == 1:
            return matches[0], None
        return None, offer(state, rows, preference_version, account_version)
    if selected:
        matches = [
            row
            for row in rows
            if row["id"] == selected["calendar_id"]
            and (row["summary"] or "Untitled calendar") == selected["label"]
        ]
        if (
            selected["preference_version"] == preference_version
            and selected["account_version"] == account_version
            and len(matches) == 1
        ):
            return matches[0], None
        return None, offer(
            state, rows, preference_version, account_version, error="calendar_choices_changed"
        )
    if len(rows) == 1:
        return rows[0], None
    return None, offer(state, rows, preference_version, account_version)


async def show(runtime):
    value = pending(runtime.state)
    if not value:
        return unavailable()
    value["last_request_id"] = runtime.request.request_id
    pref = await service.get_preferences(runtime.owner)
    rows = await service.list_calendars(runtime.owner)
    return offer(
        runtime.state,
        eligible(rows["calendars"], pref.preferences.model_dump()),
        pref.version,
        pref.account_version,
    )


async def select(runtime, choice_id):
    value = pending(runtime.state)
    if value and value.get("action_id"):
        return unavailable()
    if not value:
        runtime.state.pop("calendar_event_request", None)
        return unavailable()
    value["last_request_id"] = runtime.request.request_id
    saved = value.get("calendar_choices", {})
    option = next((row for row in saved.get("options", []) if row["choice_id"] == choice_id), None)
    if option is None:
        return unavailable()
    pref = await service.get_preferences(runtime.owner)
    rows = eligible(
        (await service.list_calendars(runtime.owner))["calendars"], pref.preferences.model_dump()
    )
    value["selected_calendar"] = {
        **option,
        "preference_version": saved["preference_version"],
        "account_version": saved["account_version"],
    }
    _, failure = resolve(runtime.state, rows, pref.version, pref.account_version)
    if failure:
        return failure
    # The click supplies only a destination reference. It cannot supply event fields
    # or promote provider labels into user-authored text.
    old_name = value["arguments"].get("calendar_name", "")
    if old_name:
        value["previous_calendar_names"] = (value.get("previous_calendar_names", []) + [old_name])[
            -10:
        ]
        value["arguments"]["calendar_name"] = ""
    from app.calendar.event_creation import prepare
    from app.calendar.intent import user_directive
    from app.schemas.conversation import PrepareCalendarEvent

    return await prepare(
        runtime,
        PrepareCalendarEvent(
            continue_previous=True,
            intent={"operation": "resume", "source": user_directive(runtime.request.instruction)},
        ),
    )
