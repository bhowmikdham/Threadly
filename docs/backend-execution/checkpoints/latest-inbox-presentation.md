# Latest Inbox retrieval and presentation

Scope: user-requested diagnosis and draft backend fix, based on maintained
`release/backend` commit `5298835bdf2a271be4ec642318f2cd95a7a81b25`.

## Findings

- The current conversation path uses live Gmail `messages.list` plus bounded
  message details. No sync database or background import supplies these results.
- When the typed folder is `INBOX`, the Gmail query includes `in:inbox` and the
  backend checks each returned message's `INBOX` label. Archived results do not
  pass that check. The reported incident's exact tool arguments were not available
  in this implementation environment, so an incorrect incident folder is unproven.
- `received_at` already comes from Gmail `internalDate`, independently of the
  email's Date header. It was passed to the model as UTC without a derived local
  value. The browser/request timezone existed in general conversation context.
- Display snippets were merely whitespace-collapsed slices of `body_clean`, so
  nested entity padding, invisible preheader text, tracking URLs and footers survived.
- An unspecified count defaulted to five. A singular newest-message intent had no
  typed representation; the existing special-case fresh-scope guard only covers a
  numbered Inbox request. This fix adds semantic tool selection without expanding
  that phrase-matching guard.
- Provider query bounds truncated fractional seconds before enforcing the exact
  local interval. A message in the final fraction of a second could be excluded by
  the provider before local filtering. Mixed-offset timestamps also sorted by
  string instead of instant, although normal live normalization already emits UTC.

## Delivered behavior

Conversation release `contextual-conversation-1.8.3` adds `search_mail.selection`:
`latest_message` resolves to one result; default `recent_matches` retains the
existing 1–5 limit. An explicit user count recognized by the existing Inbox guard
takes priority over a contradictory model selection. Prompt/tool assets are versioned in
`docs/evaluation/inbox-presentation/contextual-conversation-1.8.3.json`.

Validated `filters.timezone` travels with the search and its signed cursor.
Search cards and conversation reads retain `received_at` and add the recipient-local
ISO value, explicit display label, IANA zone and `gmail.internalDate` provenance.
ZoneInfo calculates the offset for each individual instant, including DST and
midnight transitions. The model is instructed to use the supplied display value.

The new display-only sanitizer decodes bounded nested entities, removes invisible
padding and tracking/link clutter, drops common footer lines, and converts simple
Markdown emphasis to readable text. Single meaningful word/emoji joiners survive.
Raw source bodies, source identity, exact evidence, selection and capture remain
unchanged. Provider date bounds include a one-second margin; exact `[start,end)`
filtering still controls the returned rows. Sorting compares parsed instants.

## Verification

- Focused suite: 165 passed (latest-mail, sender filtering, fresh scopes, batched
  reads, legacy inbox interpreter, conversation replay and source grounding).
- `python -m ruff check app tests`: passed.
- `git diff --check`: passed.
- Full isolated PostgreSQL suite: 1,883 passed in 242.95 seconds, no skips. This
  run preceded the final explicit-count precedence fix and evaluator strengthening.
- Final focused suite after those review changes: 145 passed; it includes the
  exact current prompt/tool snapshot contract,
  contradictory single-selection versus explicit-two request and rejects wrong
  optional 12/24-hour times, dates, DST abbreviations, offsets and answer counts.
- New synthetic replay exercises the real conversation engine with typed tool
  calls and deterministic local-time observations. Grading rejects five-card
  defaults and UTC clocks presented without the local zone. This is a scripted
  replay, not live model-quality evidence. A live Bedrock replay remains unrun.

Coverage includes Inbox versus archived mail, source timestamp versus forged Date,
Melbourne spring/autumn DST and next-day presentation, exact/subsecond date bounds,
empty/paged results, Unicode/padding/link/footer display, source preservation and
the existing source-selection, reference and citation suites.

## Limits and rollout

No migration, external write, approval change, deployment or merge is included.
The shared cursor scope now includes timezone, so pre-update cursors need a fresh
search. The legacy `/assistant/inbox-chat` interpreter keeps its historical five
results; current `/assistant/conversation-turns` uses the new semantic selection.

Search remains bounded to the requested window (rolling year by default), at most
five provider pages. Gmail documents `internalDate` as its Inbox ordering basis;
`messages.list` offers no explicit sorting parameter or complete-order guarantee.
Coverage therefore remains incomplete and labels sorting as within the returned
page. This patch cannot establish the newest message across unread provider pages
or explain the specific reported ordering without its original provider response.

Primary documentation checked: [Gmail Message resource](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages),
[messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list),
and [search filtering](https://developers.google.com/workspace/gmail/api/guides/filtering).
