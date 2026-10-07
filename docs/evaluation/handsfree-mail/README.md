# Hands-free mail continuity (conversation 1.8.8)

## Observed failure and limits

Narrow read-only incident inspection established successful latest/second-latest
Inbox retrieval, exact-phrase searches for an application and receipt that ended
without refinement, then a merchant clarification that lost the receipt goal.
Unrelated retail cards were legitimate body-keyword matches (delivery branding),
not evidence of a broken Gmail query parser. Outlook conditional XML contributed
`96`; combining grapheme joiner preheader padding survived snippet cleanup.

A named-sender lookup mixed incoming messages and a sent reply. The subsequent
request for a response to the latest message ended in HTTP 422 / the UI's
`conversation_tool_limit` retry path. No task or external mail write was recorded
for that failed request. The failed turn's individual tool arguments/sequence were
not persisted. The exact historical sequence therefore cannot be reconstructed.
Local reproduction proved that coherent informal reply wording could be rejected
by the lexical preparation gate and exhaust eight calls. The stored transcript
does not support an ASR diagnosis. These findings do not establish any actual
application outcome, receipt existence or rental obligation. Private mailbox
content, identities and production conversation/request IDs are excluded here.

## Change and boundaries

- Typed USER mail goals keep purpose, entity, sender, latest ordering and anchored
  date/folder scope across clarification. Search discovers the entity first;
  separate exact USER terms support bounded AND refinement.
- Named sender filtering uses actual incoming From metadata. Full reads and
  per-message evidence precede semantic relevance classification. Two logical
  result pages maximum, each using the existing five-provider-page fill bound.
  Answer evidence and displayed cards share the verified relevant set.
- Semantic reply preparation binds complete USER turns to a retained reference,
  checks newest incoming selection/sender ambiguity/current thread metadata and
  read scope, and hands off to the existing task. Repair reasons, bounded failure
  state and retry preserve the target; prepared retries reuse the owned task.
- The durable worker still requires explicit recipient confirmation. Replaying
  that step exposed a separate `source_not_loaded` continuation gap: chat now
  reloads the owned effective capture before its continuation transaction.
- Conditional comment metadata and repeated/boundary invisible padding are
  removed without stripping real numbers, accented text, emoji or word joiners.

Preparation is not a Gmail draft save/send and grants no new write authority.
Existing Calendar, recipient, capture, fingerprint, account, version and approval
checks remain. The new state is encrypted USER data and source references, never
original bodies/snippets/assessment quotes. No database migration is required.

## Reproduction and acceptance

`contextual-conversation-1.8.8.json` pins the full prompt/tool definitions and
hashes; historical snapshots remain unchanged. The tests in
`backend/tests/test_handsfree_mail_goals.py` use scripted model choices, synthetic
mail and `httpx.MockTransport`, real isolated PostgreSQL, the conversation service
and HTTP routes, then the real durable worker and reply artifact validation.
They replay latest → second latest → application → receipt → merchant correction
→ named sender → latest incoming reply → explicit recipient answer. Other cases
cover paraphrases, unknown results, paging, relevance/card consistency, wrong
message evidence, ambiguity, sent/stale/missing targets, scope repair, retries,
request replay, ownership/version fencing and Unicode cleanup. Every simulated
Gmail operation in this suite is GET; no draft-save/action/approval row is created.

Run from `backend/` with a dedicated disposable PostgreSQL instance (tests
truncate/drop test tables):

```sh
THREADLY_TEST_DB=<isolated-test-database> THREADLY_REQUIRE_TEST_DB=1 python -m pytest -q
python -m ruff check app tests tools
python -m app.workflows.mvp_assets --check --output fixtures/mvp/prompts-v1.json
```

`offline-replay.json` records this run's measured counts and release hashes.
Scripted semantic labels are not live-model quality evidence. No live Bedrock
behavioral evaluation, installed-client verification, real Gmail/Calendar write,
merge or deployment is claimed. This branch stacks on draft-context PR #116 at
`75a757e7ab941a0d0ad1e8f3c183a919da9b84f7`; its frontend companion is PR #115.
