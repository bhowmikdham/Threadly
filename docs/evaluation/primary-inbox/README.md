# Primary Inbox and current-day evidence

Status: implementation under review, no deployment in this follow-up.

The reported case was reproduced with bounded read-only Gmail metadata: all Inbox
categories returned a newer Promotions item, while `in:inbox category:primary`
returned the expected older Updates-labelled message. Filtering by both `INBOX`
and `CATEGORY_PERSONAL` returned no messages for that same interval. Use Gmail's
query semantics, not a local Personal-label requirement or hand-written exclusion list.
See Google's [search operators](https://support.google.com/mail/answer/7190?hl=en)
and [messages.list contract](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list).

## Behavior

- `SearchMail.inbox_category` defaults to `primary` for INBOX. Explicit `all` retains
  Promotions and other categories. All-mail/Sent remain separate. Public filters
  default to `all` for compatibility, with Primary valid only under INBOX.
- Category, owner/account, date bounds and timezone remain bound to pagination.
  Search hits still require the actual INBOX label. Meaningful prices and content,
  original Gmail `internalDate`, bodies and source-selection IDs are preserved.
- Latest mail is searched independently of today. A separate typed `today_check`
  checks the same scope from local midnight to the turn reference time. Only a
  provider-exhausted empty result permits “no matching emails today.” Incomplete
  results/timeouts/provider failures are unknown; account/auth failures still stop.
- The local-day relation reaches search, single-message reads and batch reads.
  It uses a fresh mail-turn clock even when a previous Calendar goal retains an
  earlier anchor. Melbourne's 23-hour and 25-hour DST days have correct bounds.
- Empty/invisible-only quotes are rejected. Frontend blank-source guards do not
  establish the cause of the user's empty-looking Sources: the exact production
  quotes were meaningful and the UI disclosure is initially closed. Screenshot
  bytes could not be materialized after the supported retry. No pixels were inferred.

## Verification and limits

Prompt/tool snapshots pin conversation `1.8.5` and legacy interpreter `1.2.0`.
Deterministic regressions cover Primary Updates/no-category hits, Promotions,
archived hits, cursor tampering, explicit all categories, latest singular/count,
local-midnight/DST, independent today presence/absence/unknown, bounded failures,
unchanged selection and source visibility. Scripted replay grading rejects incorrect
scope, five-result defaults, wrong local arrival dates and unsupported no-mail claims.
Live Bedrock quality evaluation is unrun; these are deterministic fixtures.

The latest search remains bounded to its displayed window (rolling year by default)
and provider-page budget. No match in that interval is not a claim of no mailbox
history. There is no provider-wide newest-order guarantee beyond the checked results.
No migration, configuration change, mail write, approval change or deployment is included.
Calendar `1.8.4` and classification PR105/106 remain in the integration base.

Local integrated backend verification: **2,085 tests passed, zero skips**, against
isolated PostgreSQL16 (257.63s). Ruff and diff checks passed. The full run included
classification and Calendar code; final dependency reconciliation changes only the
Calendar integration evidence documents. Hosted CI and exact heads are tracked in
the draft PR. No live Bedrock replay or new production verification was performed.
