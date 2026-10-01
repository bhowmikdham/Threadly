# Semantic Calendar interpretation — 1.4.0

## Failure and correction

The reported `am i free tmrw ?` and `am i free tommorrow` requests missed the
old whole-day regex fast path. Model-selected reads then failed because normalized
`tomorrow` was not literally present in the request. A local spelling-alias patch
was discarded before publication; no dictionary was shipped.

The conversation engine now always uses the semantic coordinator. The old day
question recognizer is removed. Tools take a structured date interpretation and
an original source quote. Date computation, timezone/DST, ownership, range limits,
actual busy periods, partial coverage and approval remain backend-owned. Numeric
clock interpretations likewise have separate quoted sources. The model can interpret
wording; a source quote alone is not proof its interpretation is correct.

## Evidence

- Reproduced both original failures before changes.
- Broader backend regression during development: 1,602 passed with a disposable
  PostgreSQL database, zero skips (380.30 seconds).
- Final affected Calendar/conversation/model suite: 187 passed, zero skips, including
  database ownership, preference races and persisted relative-date anchors.
- Ruff passed. Historical regex-specific tests were replaced with model-dispatch and
  structured-input checks; existing freebusy, ownership and partial-coverage tests remain.
- `live-semantic-v1.json` and `live-semantic-weekday-debug.json` retain the failed
  natural-language canonical-date attempt. `live-semantic-v2.json` captures the
  subsequent equivalent-clock-format failure. These are diagnostic attempts, not
  release passes.
- `live-semantic-v3.json`: all 7 date/clock live Bedrock cases passed at that intermediate revision
  (its prompt hash is retained). `live-semantic-v5.json` is the final nine-case gate against the final prompt/tools
  and real runtime/date handlers with synthetic freebusy evidence. Includes both user
  reports, ordinary tomorrow, unseen paraphrase, relative offset, next weekday and
  an explicit 2–4 PM clock window. No spelling lookup is in the runtime path.
- `live-semantic-scope-v1.json` and `live-semantic-v4.json` exposed a separate other-person
  interpretation failure. `live-semantic-scope-v2.json` verifies the correction: structured
  calls explicitly identify self/other, and the backend refuses other-person reads before
  accessing preferences or the provider. Cancellation remains model-decided with no read.
- Prompt/tools snapshot: `contextual-conversation-1.4.0.json`; replay cases:
  `replay-v2.json` and `../calendar-day-availability-v3.json`.

CI runs the complete final revision with PostgreSQL before merge. No Google Calendar
or Gmail read/write is performed by the synthetic model replay. Real-account Google
behavior and untested linguistic ambiguity are not established by these results.
There is no migration, extension update, new model, new AWS resource, RAG index or
classifier dependency. The backend deploy activates the new contract.
