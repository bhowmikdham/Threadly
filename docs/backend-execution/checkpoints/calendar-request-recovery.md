# Calendar request recovery

Scoped follow-up to the reported Thursday availability / invalid master proposal failure.
Baseline: PR #69, `707d5570ca3230232647548dbf82742134085989`.

## Behavior

- Direct reads cover both screenshot phrasings and the prior typo.
- Argument-free semantic availability tool handles other self-day paraphrases.
- Dates and ownership are backend controlled; compound and ambiguous windows
  remain reviewed workflows or clarifications.
- Partial Calendar coverage displays verified busy times and the unchecked
  calendar names without changing the user's selections or claiming free time.
- Failed/expired proposal responses no longer claim there is work to review.
- Companion frontend change replaces internal failure codes and adds a retry
  button; setup banner says Calendar connected rather than implying every read succeeds.

## Verification

Local full backend: 926 passed, 608 skipped before final replay/scope assertions.
Final focused day-availability checks: 62 passed. PostgreSQL unavailable locally;
DB acceptance remains a CI dependency. No migration or permission change.
Frontend: 101 tests passed; TypeScript and production build passed.
Versioned replay: `docs/evaluation/calendar-day-availability-v2.json`.

Sanitized live diagnosis: three selected calendars returned known free/busy;
one selected holiday calendar returned a provider error. No credentials, calendar
IDs, event details or raw provider errors are included here.

## Focused review

No model-supplied dates/IDs, no external writes or selection mutations. Unknown
coverage retains its warning even when known calendars contain busy intervals.
Calendar names are rendered as text and unavailable as instructions in future
model history. Previous evidence deserializes with an optional absent name.
