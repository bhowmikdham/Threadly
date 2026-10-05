"""Live Bedrock follow-up decisions against synthetic Calendar evidence only."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.calendar import service
from app.config import get_settings
from app.conversation import calendar_context, engine
from app.conversation.evaluate import validate_live_preflight
from app.conversation.prompt import assets
from app.conversation.runtime import Runtime, model_history
from app.schemas.calendar import CalendarCoverage, FreeBusyOut, Preferences
from app.schemas.calendar_tools import FindFreeTimes
from app.schemas.conversation import CheckDayAvailability

ANCHOR = datetime(2026, 10, 5, 6, 55, tzinfo=UTC)
CASES = [
    ("check_now", "check now", "2026-10-06", "day"),
    ("repaired", "I've fixed it, check again", "2026-10-06", "day"),
    ("paraphrase", "Could you give it another go?", "2026-10-06", "day"),
    ("legacy", "check now", "2026-10-06", "legacy"),
    ("new_day", "What about Friday instead?", "2026-10-09", "day"),
    ("clock", "check now", "2026-10-06T14:00", "clock"),
]


async def evaluate():
    results = []
    zone = ZoneInfo("Australia/Melbourne")
    for name, followup, expected, mode in CASES:
        if results:
            await asyncio.sleep(8)
        reads, decisions = [], []
        provider = {"repaired": False}
        preferences = Preferences(
            timezone=str(zone),
            calendar_ids=["synthetic"],
            buffer_before_minutes=0,
            buffer_after_minutes=0,
            minimum_notice_minutes=0,
            default_duration_minutes=30,
            working_periods=[
                {"weekday": n, "start_minute": 540, "end_minute": 1020} for n in range(5)
            ],
        )

        async def prefs(owner, preferences=preferences, provider=provider):
            assert owner == 42
            return SimpleNamespace(
                preferences=preferences, version=2 if provider["repaired"] else 1, account_version=1
            )

        async def freebusy(owner, body, reads=reads, provider=provider):
            assert owner == 42
            reads.append(body)
            return FreeBusyOut(
                id="synthetic-evidence",
                preferences_version=body.expected_preferences_version,
                account_version=1,
                policy_version="synthetic",
                checked_at=ANCHOR,
                expires_at=ANCHOR + timedelta(minutes=5),
                start=body.start,
                end=body.end,
                coverage="complete" if provider["repaired"] else "unknown",
                calendars=[
                    CalendarCoverage(
                        calendar_id="synthetic",
                        display_name="Synthetic holiday calendar",
                        status="known" if provider["repaired"] else "unknown",
                        reason=None if provider["repaired"] else "provider_error",
                        busy=[],
                    )
                ],
            )

        class ReadOnlyRuntime(Runtime):
            async def call(self, tool, args, decisions=decisions):
                decisions.append({"tool": tool, "arguments": args.model_dump(mode="json")})
                if tool not in {
                    "retry_calendar_read",
                    "check_day_availability",
                    "find_busy_times",
                    "find_free_times",
                }:
                    raise ValueError("Replay permits availability reads only")
                return await super().call(tool, args)

        state = {"history": [], "refs": {}, "calendar_read_anchor": ANCHOR.isoformat()}
        question = "check if i am free tmrw?"
        args = dict(subject="self", date={"kind": "relative", "offset_days": 1}, date_source="tmrw")
        tool = "check_day_availability"
        parsed = CheckDayAvailability(**args)
        if mode == "clock":
            question = "Find slots tmrw from 2 pm to 4 pm for 30 minutes"
            tool = "find_free_times"
            parsed = FindFreeTimes(
                **args,
                start_time="14:00",
                end_time="16:00",
                start_time_source="2 pm",
                end_time_source="4 pm",
                duration_phrase="30 minutes",
            )
        with (
            patch.object(service, "get_preferences", prefs),
            patch.object(service, "query_freebusy", freebusy),
        ):
            # Seed the original request through the real backend handler. The live
            # model chooses only the follow-up; no mail or Google adapters are used.
            initial = Runtime(
                42, SimpleNamespace(instruction=question, request_id="first"), state, None
            )
            answer = await initial.call(tool, parsed)
            state["history"] = [
                {
                    "user": question,
                    "assistant": answer["text"],
                    "source": "calendar_availability" if mode != "clock" else "calendar_tools",
                    "kind": "message",
                }
            ]
            if mode == "legacy":
                state[calendar_context.KEY]["arguments"] = None
                state[calendar_context.KEY]["earliest_anchor"] = ANCHOR.isoformat()
                state["history"].append(
                    {"user": "check now", "assistant": "Which date?", "kind": "clarification"}
                )
            provider["repaired"] = True
            now = ANCHOR + timedelta(minutes=3)
            state["calendar_read_anchor"] = now.isoformat()
            runtime = ReadOnlyRuntime(
                42, SimpleNamespace(instruction=followup, request_id=str(uuid4())), state, None
            )
            context = {
                "user_turn": followup,
                "recent_dialogue": model_history(state["history"]),
                "previous_calendar_request": calendar_context.model_context(state),
                "active_work": None,
                "selected_reference": None,
                "now": now.isoformat(),
                "timezone": str(zone),
                "capabilities": {"calendar_read": True, "calendar_events_read": True},
            }
            response = await engine.run(context, runtime)
        new_reads = reads[1:]
        starts = [r.start.astimezone(zone).isoformat() for r in new_reads]
        ends = [r.end.astimezone(zone).isoformat() for r in new_reads]
        passed = (
            response.get("kind") == "message"
            and len(new_reads) == 1
            and starts[0].startswith(expected)
            and new_reads[0].expected_preferences_version == 2
        )
        if mode == "clock":
            passed = passed and ends[0].startswith("2026-10-06T16:00")
        results.append(
            {
                "case": name,
                "user_turn": followup,
                "decisions": decisions,
                "starts": starts,
                "ends": ends,
                "text": response.get("text"),
                "trace": response.get("trace"),
                "passed": passed,
            }
        )
        print(json.dumps(results[-1]), flush=True)
    return {
        **assets(),
        "model": get_settings().bedrock_model_id,
        "anchor": ANCHOR.isoformat(),
        "live_google": False,
        "external_writes": False,
        "initial_request": "seeded through real handler; follow-up decision uses live model",
        "cases": results,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Use --live for bounded synthetic Bedrock replays")
    validate_live_preflight(get_settings())
    result = asyncio.run(evaluate())
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if result["passed"] != result["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
