# Reply authorship and source grounding

The [combined review](../../review/combined-voice-security.md) supersedes this
initial record's PostgreSQL skip limitation: the full combined suite now passes
2,482 tests with a disposable PostgreSQL instance and zero skips.

Local candidate based on released maintenance commit `1cb7328`. All mail identities,
bodies, model decisions and generated replies in the regression tests are synthetic.
No live inference, Gmail writes, deployment or publication was performed.

## Behavior

- `read_email` identifies enclosing message speakers using backend-owned
  `is_from_user`/`author_role` and marks the selected reply target independently.
- Draft generation includes the mailbox-owner author and speaker roles in both
  context-plan and older snapshot paths. Older saved snapshots can derive the
  role from the authenticated envelope's From address. Envelope addresses/Bcc
  are not copied into this new prompt metadata.
- The reply prompt says to write as the owner to the correspondent, reconciling
  earlier owner messages and later acknowledgments. The verified compose prompt
  is unchanged. Quoted source instructions cannot select a role or recipient.
- A selected-email reply cannot bypass its source read through standalone compose,
  even with `request_source`. It also cannot recover through a prose-only draft.
  A genuinely independent new email remains supported while a source is pinned.
- Recipient authority, source ownership/freshness, exact reply subject, immutable
  review, approval, draft-saving and send boundaries retain their existing checks.

The regression first reproduced missing perspective labels in both snapshot paths
and an unread selected reply becoming a user-only draft addressed to “message”.
Those four tests failed on the original code and pass with this candidate. This
reproduces contract defects, not a paid model's interpretation of the reported mail.

## Versioned assets

`contextual-conversation-1.8.10+reply-perspective.1.json` pins conversation instructions
and tools; `draft-artifact-1.2.3.json` pins the generation prompts and schemas.
The lookup-draft template is `lookup-draft-template-1.2.3`, with a new fixture.
Historical live evidence and previous fixtures remain unchanged. The historical
live-evaluation test now explicitly checks the evaluated older reply release and
does not claim that the new reply prompt received live model acceptance.

## Verification

Using the existing local Python environment, from `backend/`:

```sh
python -m ruff check app tests
python -m pytest -q tests/test_reply_perspective.py tests/test_reply_perspective_db.py
THREADLY_TEST_DB=postgresql+asyncpg://threadly:synthetic@127.0.0.1:1/threadly_voice_tests \
  python -m pytest -q
```

Ruff: passed. The full offline suite: **1,481 passed, 989 skipped**. The deliberately
unavailable database prevents mutation of any shared developer database; PostgreSQL
cases, including the two new source-capture/idempotent-worker cases, remain unverified.
Loopback-only HTTP tests pass outside the filesystem/network sandbox. Unit replay
covers read-before-reply repair, refusal of prose fallback, repeated mistaken compose
calls, retention of prior goal state, distinct speaker roles, quoted text and aliases.

## Remaining evidence

The provided Library screenshot could not be materialized: the supported download
reported `download failed`, including its one permitted consumer-local retry. It was
not viewed. No assumption was made that a supplied candidate reply was erroneous.
The production model's exact failure remains unverified because paid model calls and
real mailbox access/writes were not authorized. Live model acceptance of the new
prompt is a later gate, not implied by the deterministic regression results.
