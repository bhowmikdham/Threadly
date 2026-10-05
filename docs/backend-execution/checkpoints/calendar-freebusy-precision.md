# Calendar freeBusy timestamp precision repair

Status: implemented, in review; no merge or deployment.
Scope: B12 read-adapter correction, also used by B15 event preflight.
Branch: `codex/calendar-freebusy-precision`.
Base: `41eeaa8c7639d3a0bb60552cfac613322362d816`.
The earlier creation/recovery draft PR #94 at `06fd451` is unchanged.

## Reproduced failure

At 00:36–00:37 Melbourne on 6 October 2026, both `check my availability today`
and `am i busy today` returned `calendar_response_invalid`. Read-only production
diagnostics reported existing Calendar access ready and the deployed revision
`8a9e72c`. A bounded read-only probe reported that Google accepted a start ending
in `.937225+00:00` but echoed `.937Z`; a whole-second start passed the same
normalizer. These observations were supplied by the coordinating investigation;
this worktree made no live Google requests or credential changes.

The original adapter compared Google's echo against the unmodified microsecond
anchor. Local fake-provider replay reproduced the exact user-facing error for
both phrases, with busy and empty calendars. Before repair: 7 failed, 4 passed.
This is a transport precision error, independent of the creation-routing fix,
consent, installed extension package, or a wrong Melbourne civil date.

## Changed behavior and review

- Convert bounds to UTC and floor the outgoing start / ceil a fractional outgoing
  end to whole seconds, covering the entire original request.
- Keep exact echo validation against that transmitted window. Even a 1 ms shifted,
  narrowed or enlarged response still fails closed.
- Clip normalized busy intervals back to the original requested instants. Padding
  alone is not busy time, and tiny intervals/cross-midnight requests do not collapse.
- Preserve selected IDs, unknown coverage, ownership, evidence version/freshness
  fences, response limits, approval semantics and the event worker's write boundary.
- Persist exact original evidence bounds. No schema/configuration/permission change;
  no policy, prompt, tool or model release change and no new retry behavior.

Second local review traced both callers (`calendar/service.py` and
`actions/calendar_executor.py`), half-open interval clipping/merging, unknown
coverage, and strict echo comparison. Transport padding cannot bypass an original
busy interval or make unknown coverage known. No provider write path was added.
Final adapter SHA-256:
`197ba5bafb411cefc2f769f0efa084123a1ff81c4e4fcf0e574c8e470122ab42`.

## Verification

- Focused client/service/day-answer/creation suite during implementation: **160
  passed, 0 skipped**, 32.61 s; isolated PostgreSQL 16 and mocked Google transport.
- Final adapter precision replay: **14 passed, 0 skipped**, 0.14 s. Includes both
  exact reported phrases, busy/empty answers, fractional evidence bounds, tiny
  intervals, midnight crossing, UTC offset input, partial coverage, removal of
  padding, aligned bounds and 1 ms response mismatch rejection.
- Added route → service → real PostgreSQL regression proving exact fractional
  bounds survive provider normalization and stored evidence roundtrip. The shared
  service fixture now emulates Google's millisecond timestamp serialization.
- Ruff (`backend/app`, `backend/tests`, `backend/tools`), `git diff --check`, playbook
  validation, generated-card check and backend-handoff validation passed.
- Final full backend regression runs with `THREADLY_REQUIRE_TEST_DB=1` on the
  dedicated disposable database `threadly-calendar-precision-test`, port 64727.
  Its terminal result and exact-head CI evidence are recorded in the PR body;
  this checkpoint does not assert an unfinished run passed.
- Local artifacts: `/tmp/threadly-calendar-precision-before.log`,
  `/tmp/threadly-calendar-precision-focused.xml`,
  `/tmp/threadly-calendar-precision-unit.xml`,
  `/tmp/threadly-calendar-precision-full.xml`.
- Two existing local FastAPI/Starlette dependency deprecation warnings observed.
  No live model evaluation was run; fake semantic/provider fixtures are not model
  quality scores. No production smoke of the repaired revision has occurred.

## Next gate

Review the focused draft and terminal exact-head checks. The creation/recovery
feature draft and its frontend picker can be verified together separately; this
adapter patch has no frontend dependency. Release remains a separate gate.
