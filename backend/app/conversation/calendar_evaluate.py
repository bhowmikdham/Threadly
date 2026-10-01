"""Bounded semantic Calendar replay: live Bedrock, synthetic evidence, no Google access."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.calendar import service
from app.config import get_settings
from app.conversation import engine
from app.conversation.evaluate import validate_live_preflight
from app.conversation.prompt import assets
from app.conversation.runtime import Runtime
from app.schemas.calendar import CalendarCoverage, FreeBusyOut, Preferences

ANCHOR = datetime(2026, 10, 1, 12, 19, tzinfo=UTC)
CASES = [
    ("abbreviation", "am i free tmrw ?", "2026-10-02", "2026-10-03"),
    ("misspelling", "am i free tommorrow", "2026-10-02", "2026-10-03"),
    ("ordinary", "am i free tomorrow", "2026-10-02", "2026-10-03"),
    (
        "paraphrase",
        "How's my availability looking for the day following today?",
        "2026-10-02",
        "2026-10-03",
    ),
    ("relative", "Am I free the day after tomorrow?", "2026-10-03", "2026-10-04"),
    ("weekday", "check if i am free on thursday next week", "2026-10-08", "2026-10-09"),
    ("cancelled", "Do not check my calendar tomorrow", None, None),
    ("other_person", "Is Alex free tomorrow?", None, None),
    ("clock", "Am I free tmrw from 2 pm to 4 pm?", "2026-10-02T14:00", "2026-10-02T16:00"),
]


async def evaluate(only=None):
    from zoneinfo import ZoneInfo

    results = []
    zone = ZoneInfo("Australia/Melbourne")
    for case, instruction, expected_start, expected_end in CASES:
        if only and case not in only:
            continue
        if results:
            await asyncio.sleep(8)
        reads, decisions = [], []
        prefs = Preferences(
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

        async def preferences(owner, prefs=prefs):
            return SimpleNamespace(preferences=prefs, version=1, account_version=1)

        async def freebusy(owner, request, reads=reads):
            reads.append(request)
            return FreeBusyOut(
                id="synthetic-evidence",
                preferences_version=1,
                account_version=1,
                policy_version="synthetic",
                checked_at=ANCHOR,
                expires_at=ANCHOR + timedelta(minutes=5),
                start=request.start,
                end=request.end,
                coverage="complete",
                calendars=[CalendarCoverage(calendar_id="synthetic", status="known", busy=[])],
            )

        class ReadOnlyRuntime(Runtime):
            async def call(self, name, args, decisions=decisions):
                decisions.append({"tool": name, "arguments": args.model_dump()})
                if name not in {"check_day_availability", "find_busy_times", "find_free_times"}:
                    raise ValueError("This replay allows availability reads only")
                return await super().call(name, args)

        runtime = ReadOnlyRuntime(
            1,
            SimpleNamespace(instruction=instruction),
            {"history": [], "refs": {}, "calendar_read_anchor": ANCHOR.isoformat()},
            None,
        )
        context = {
            "user_turn": instruction,
            "recent_dialogue": [],
            "active_work": None,
            "selected_reference": None,
            "now": ANCHOR.isoformat(),
            "timezone": str(zone),
            "capabilities": {"calendar_read": True, "calendar_events_read": True},
        }
        with (
            patch.object(service, "get_preferences", preferences),
            patch.object(service, "query_freebusy", freebusy),
        ):
            response = await engine.run(context, runtime)
        starts = [r.start.astimezone(zone).isoformat() for r in reads]
        ends = [r.end.astimezone(zone).isoformat() for r in reads]
        passed = (
            response.get("kind") == "message"
            and len(reads) == 1
            and expected_start is not None
            and starts[0].startswith(expected_start)
            and ends[0].startswith(expected_end)
        )
        if expected_start is None:
            passed = not reads and response.get("kind") in {"message", "clarification"}

        results.append(
            {
                "case": case,
                "decisions": decisions,
                "text": response.get("text"),
                "passed": passed,
                "kind": response.get("kind"),
                "trace": response.get("trace"),
                "starts": starts,
                "ends": ends,
            }
        )
        print(json.dumps(results[-1]), flush=True)
    return {
        **assets(),
        "model": get_settings().bedrock_model_id,
        "anchor": ANCHOR.isoformat(),
        "live_google": False,
        "external_writes": False,
        "cases": results,
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--case", action="append", choices=[c[0] for c in CASES])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Use --live for bounded synthetic Bedrock replays")
    validate_live_preflight(get_settings())
    receipt = asyncio.run(evaluate(args.case))
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    if receipt["passed"] != receipt["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
