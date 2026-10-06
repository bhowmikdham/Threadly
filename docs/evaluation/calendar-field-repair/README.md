# Calendar field repair and availability presentation

Release `contextual-conversation-1.8.4` separates invalid model extraction from
information the user has not supplied. A rejected event field returns sanitized
repair feedback through the bounded tool loop. A titleless request retains its
valid date/time and asks only for the title. Valid source constraints and exact
approval ownership remain enforced.

Single-time availability leads with free/busy/uncertain and includes an additive,
transient card payload with date/time, scope, duration and evidence freshness.
Partial evidence cannot confirm free time; busy-only evidence supplies no titles.

`contextual-conversation-1.8.4.json` pins the prompt and tools. `receipt.json`
records deterministic replay evidence and source hashes. All data are synthetic;
no production messages, provider content, request identifiers or credentials are
included here.

Run from `backend/`, using a disposable PostgreSQL database:

```sh
THREADLY_TEST_DB=postgresql+asyncpg://USER@HOST/TEST_DB THREADLY_REQUIRE_TEST_DB=1 \
  python -m pytest -q tests/test_calendar_field_repair.py \
  tests/test_calendar_availability_presentation.py
THREADLY_TEST_DB=postgresql+asyncpg://USER@HOST/TEST_DB THREADLY_REQUIRE_TEST_DB=1 \
  python -m pytest -q
python -m ruff check app tests tools
```

The initial complete backend suite passed **1917 tests, zero failures or skips**.
Ruff passed. Tests use scripted model decisions and fake Google adapters through
the real application/database paths. This verifies repair handling, not the live
model's probability of generating the corrected call. No live Bedrock evaluation,
Google write, deployment or merge was performed.

The later release-branch integration preserves the Haiku classification source
from `82225a5` without modification. Its complete backend suite passed **2014
tests, zero failures or skips**, with Ruff and versioned MVP assets passing.
The recorded classification predictions replay without a live model call;
the existing seed-evaluation quality limitation remains. See
`classification-integration.json` for the integration receipt. This merges
upstream source into the draft review branch only, not into a release branch.

Coverage includes literal title/date/time/duration/location/description/calendar
and attendee mismatches, normalized clock conflict, invalid schema privacy,
bounded exhaustion without candidate mutation, missing-title continuation, and a
new cricket goal after an older approved synthetic event without reusing approval.
Availability covers free/busy, later overlap, requested/default duration, partial
coverage, stale/failing providers, another person's scope, and Melbourne midnight.
