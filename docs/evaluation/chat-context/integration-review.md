# Combined context and meeting-email review

The context stack and meeting-email button are integrated locally. Nothing is
published, merged into release branches or deployed. The original button commits
remain preserved on their independent branches. The approved diagnostic consumed
18 attempts (17 completed, one throttled), with partial recall/goal/citation successes
but no end-to-end scenario completion. See [actual outcomes](approved-evaluation-status.md).

## Commit lineage

Backend branch `codex/chat-context-continuity`:

- Released voice base: `bc12ef106659f5ac8b5b79890e0887f1431e29ea`.
- Context implementation: `82d3e4a`.
- Meeting-email implementation: `2768f25`, cherry-picked from preserved `53a0d11`.
- Combined approval regression: `4ae5f730dfb197acaa2b31af35624203ad202356`.

Frontend branch `codex/chat-context-restoration`:

- Released voice base: `afa2180fa16b4060587002b6be440ce32573c603`.
- Context protocol/restoration: `34391a2`.
- Meeting-email editor: `53e1770`, cherry-picked from preserved `fb6d8d5`.
- Combined browser regression: `73d6e491a82105926b93b22be72aef172b1d5e01`.

Later commits `e3c297d` and `dfb32f9` repair supported token counting, durable call
accounting and credential setup. `744982f` repairs new-email-goal feedback and versions
the prompt/tools as `.3`; it has no live-model evaluation. The machine-readable receipt
pins test heads, source hashes and the boundaries of full versus focused checks.

## Ownership, provenance and approvals

The email-button API derives the owner from authentication. It reads the exact
owned Gmail thread/message, captures reference-only identity, verifies that the
selected message belongs to that thread, and checks fresh fingerprints before
preview, approval and dispatch. Its editable fields are separate from chat
citations and goal state. Source instructions cannot choose attendees or consent.
Generated event payloads are durable; original email bodies are not copied into
new persistent records.

Calendar candidates use the existing immutable payload hash, action version,
expiry, source/ACL/account/session checks, approval receipt and durable dispatch
job. The button always sets `approval_mode: ask`; its blocker branch runs before
the direct conversational-event branch. Opening, previewing and restoring status
cannot approve or enqueue an event. Editing rejects the old version first, and
an approval race blocks the edit. Uncertain outcomes use the existing reconciliation
path and saved action identity rather than silently making another event.

`test_chat_context_meeting_email.py` proves the combined boundary against real
isolated PostgreSQL and fake Google: independent email and Calendar chat goals
survive opening/previewing an email event unchanged. Resuming the chat Calendar
under Always can approve that direct event, while the separate email-button action
remains proposed with no approval and no job. No provider insert occurs in the test.

The extension browser test uses `context_memory_version: 1`, edits/reviews/confirms
an email event under Always, then sends a follow-up in the same chat. The next turn
keeps the conversation/version without injecting the button's task or source as
chat focus, and it creates no second approval. Backend-owned focus protocol and
explicit attach/detach behavior retain their unit coverage.

The released backend voice route/tests and frontend VoiceOrb remain unchanged
from their release bases. The optional CalendarEventCard state callback reports
the existing action lifecycle; it changes neither the approval payload nor speech
session handling.

## Remaining architectural and release limits

Encrypted archival preserves original completed turns within the owned chat's
retention period. It does **not** provide arbitrary universal recall. Recent context,
goal listing, literal retrieval and turn indexes are bounded and paginated. Their
usefulness depends on the model selecting relevant goals, terms and pages; a detail
or keyword-free correction outside what it inspects can still be missed. The model
must not equate an incomplete search with absence of a fact.

Verified citations establish ownership, authorship and original clock provenance;
they do not prove semantic relevance, correct pronoun resolution or current consent.
A later correction can conflict with an earlier valid quote. The diagnostic correctly
handled one synthetic later correction and one explicit goal return; broader semantic
reliability remains unproven. Some legacy/compound intent gates retain
lexical rules, and production context budgeting uses characters rather than token
counts. Historical citations do not expand deterministic scheduling or source-quoted
plan-item validators. There is no cross-chat memory or full archived-transcript UI.

The button requires manual date/time and attendee choices; it does not extract
arbitrary meeting details, create recurring/all-day events or edit existing events.
Its source must remain accessible during review. Fake-provider tests do not certify
live Google account compatibility.

The preflight issue is resolved and the initial diagnostic is reviewed. Its failures
require a newly approved, adequately bounded live evaluation of `.3`, including held-out
paraphrases, completed drafts and saved-artifact restoration. All 18 attempts are used.
A future clean diagnostic is still not a release pass. Before any future publication/deployment,
review the final changes and perform the required release checks. Migration
`h071026e9043` is additive but old backend writers cannot safely preserve new-format
goals/timezones. API and workers must switch together after draining work; rollback
requires a compatibility build, not dropping retained context. No rollout is authorized
by this review or by the model-evaluation approval.
