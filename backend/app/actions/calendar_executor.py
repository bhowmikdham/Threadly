"""One event insert or exact-ID reconciliation. No blind insert retry."""

import asyncio
import json
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.actions import service
from app.actions.calendar_preview import SCHEMA
from app.calendar import client
from app.config import get_settings
from app.model_client.structured import reject_duplicate_keys
from app.schemas.calendar import parse_instant


def enabled(owner, transport=None):
    settings = get_settings()
    return settings.calendar_writes_enabled and (
        isinstance(transport, httpx.MockTransport)
        or str(owner) in settings.write_pilot_user_ids_values
    )


@dataclass(frozen=True)
class Outcome:
    state: str
    code: str
    event_id: str | None = None

    def evidence(self):
        return {"state": self.state, "code": self.code, "event_id": self.event_id}


def frozen(action):
    if (
        action.action_type != "create_event"
        or action.payload_schema != SCHEMA
        or service.candidate_hash(SCHEMA, action.payload) != action.payload_hash
    ):
        raise ValueError("Invalid event payload")
    return action.payload


def matching(actual, request):
    expected = request["event"]
    try:
        return (
            actual["id"] == expected["id"]
            and actual.get("status") != "cancelled"
            and actual.get("extendedProperties", {}).get("private", {}).get("threadlyAction")
            == expected["extendedProperties"]["private"]["threadlyAction"]
            and all(
                actual.get(k, "") == expected[k] for k in ("summary", "description", "location")
            )
            and all(
                parse_instant(actual[k]["dateTime"]) == parse_instant(expected[k]["dateTime"])
                and actual[k].get("timeZone") == expected[k]["timeZone"]
                for k in ("start", "end")
            )
            and sorted(a["email"].casefold() for a in actual.get("attendees", []))
            == sorted(a["email"] for a in expected["attendees"])
            and all(
                actual.get(k, default) == expected[k]
                for k, default in (
                    ("guestsCanModify", False),
                    ("guestsCanInviteOthers", True),
                    ("guestsCanSeeOtherGuests", True),
                )
            )
            and actual.get("reminders", {}).get("useDefault") is False
            and not actual.get("reminders", {}).get("overrides")
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


async def preflight(token, request, preferences, *, transport=None):
    calendars = await client.list_calendars(token, transport=transport)
    destination = next((c for c in calendars if c["id"] == request["calendar_id"]), None)
    if destination is None or not destination["event_write_acl"]:
        return "calendar_write_acl_missing"
    from datetime import timedelta

    start = parse_instant(request["event"]["start"]["dateTime"]) - timedelta(
        minutes=preferences["buffer_before_minutes"]
    )
    end = parse_instant(request["event"]["end"]["dateTime"]) + timedelta(
        minutes=preferences["buffer_after_minutes"]
    )
    coverage = await client.freebusy(
        token, preferences["calendar_ids"], start, end, transport=transport
    )
    if any(c.status != "known" for c in coverage):
        return "calendar_coverage_unknown"
    if any(c.busy for c in coverage):
        return "calendar_busy_after_approval"
    return None


async def request_event(token, request, *, read=False, transport=None, owner=None):
    if not read and not enabled(owner, transport):
        return Outcome("failed", "calendar_writes_disabled_before_http")
    path = client.BASE + "/calendars/" + quote(request["calendar_id"], safe="") + "/events"
    if read:
        path += "/" + quote(request["event"]["id"], safe="")
    try:
        async with asyncio.timeout(35):
            async with httpx.AsyncClient(
                timeout=30, transport=transport, follow_redirects=False, trust_env=False
            ) as http:
                async with http.stream(
                    "GET" if read else "POST",
                    path,
                    headers={"Authorization": f"Bearer {token}"},
                    **(
                        {}
                        if read
                        else {
                            "params": {"sendUpdates": request["send_updates"]},
                            "json": request["event"],
                        }
                    ),
                ) as response:
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        data.extend(chunk)
                        if len(data) > 128000:
                            return Outcome("outcome_unknown", "calendar_response_limit")
                    if read and response.status_code == 404:
                        return Outcome("outcome_unknown", "calendar_event_not_observed")
                    body = json.loads(data, object_pairs_hook=reject_duplicate_keys)
                    if response.status_code in {200, 201}:
                        if matching(body, request):
                            return Outcome(
                                "succeeded", "calendar_event_matched", request["event"]["id"]
                            )
                        return Outcome("outcome_unknown", "calendar_event_identity_conflict")
                    # ID collision is not evidence of this action succeeding; read the exact ID.
                    if (
                        not read
                        and response.status_code in {400, 401, 403}
                        and isinstance(body, dict)
                        and isinstance(body.get("error"), dict)
                        and body["error"].get("code") == response.status_code
                    ):
                        return Outcome("failed", "calendar_rejected_" + str(response.status_code))
                    return Outcome("outcome_unknown", "calendar_unconfirmed_response")
    except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError):
        return Outcome("outcome_unknown", "calendar_transport_uncertain")
