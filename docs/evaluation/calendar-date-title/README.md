# Calendar date and title grounding

Backend base: `daf5d42928093b4d6a6e9e9ef4f5fd4c522bcbe8`.
Frontend base checked independently: `e0fbc65b0fce6f98a29503af022586265dd3d0d3`;
no frontend changes are required.

The recorded first-turn request “create an event on the next wednesday at 3pm for
a class” produced a Thursday 15 October 2026 preview. At the saved 8 October
Melbourne anchor, next Wednesday is 14 October. The stored candidate already had
the wrong date; rendering that timestamp in Melbourne correctly displays Thursday.

**Evidence limit:** existing preparation canonicalizes dates to absolute values
before persistence. The retained absolute `2026-10-15` therefore does not establish
the model's original date representation. The regressions cover wrong absolute,
weekday and relative interpretations. They do not reproduce an unavailable raw
model response or claim live model acceptance.

Policy `direct-calendar-event-1.2.1` checks recognized date constraints against the
resolved local day before state replacement or action retirement. Rejected dates
enter the existing bounded field-repair loop. Old actions remain intact until a
valid correction is prepared; other grounded new-draft fields survive repair.
Relative date provenance retains its original anchor timezone, including a scoped
compatibility lookup for older owned previews that lack that metadata.

Title cleanup allows omission of an unquoted lowercase `a`/`an` before a lowercase
word in an unambiguous trailing title phrase. It preserves all substantive words,
quoted or explicitly named titles, capitalized names and definite articles. This
is a grammatical rule, not an expanding list of event nouns.

## Verification

- [Release baseline replay](red-before.log): **23 failed, 25 passed, no skips**.
  Twenty-one failures expose the date/title behavior; two exercise the added
  anchor-zone compatibility entry point, absent from the baseline.
- [Final focused checks](focused-green.log): **69 passed, no skips** (new grounding
  cases plus the calendar-choice suite).
- [Broader related checks](related-green.log): **248 passed, no skips**, before the
  final two quoted-title boundary cases were added; those are in the focused run.
- Ruff and the repository documentation validator pass.
- Full exact-head backend and CI results are reported in the pull request.

Tests use PostgreSQL 16.2 in a separate disposable database, scripted model
decisions and fake Google transports. No paid inference, provider write, production
change, OAuth change or migration was performed. One existing Starlette test-client
deprecation warning is present.

The consistency fence deliberately covers recognized English weekday/week
qualifiers, ISO dates and supported relative-day phrases. Unrecognized language
continues through the existing semantic path; this is not a claim of universal
date-language validation. Existing incorrect previews are not silently rewritten.
