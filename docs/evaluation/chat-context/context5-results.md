# .5 diagnostic: stopped on retained-purpose regression

The run stopped after **3 of 12 approved paid attempts**, all completed by the
provider. Estimated inference cost including the 10% GST allowance:
**USD 0.10397772** (84,022 input / 382 output tokens). Nine attempts remain unused:
three email and six Calendar. They are not permission for an automatic restart,
new window or reset after the agreed semantic stop.

Approval: [recorded scope](approved-context5-diagnostic.md). Harness head:
`90dd4bb4400924af5c5ea74c33bb6ad25a271d4e`. Application runtime remained
`7fb055f409c46479bc1cd1194aa870ff93ccf187`, prompt `.5`; frontend remained
`86af12f5e26aca9cb8a9a8c2ba76e1d2cfdd60bf`. Both original exhausted ledgers are
unchanged. No production change, provider write, approval/job or deployment occurred.

AWS login was renewed before the run. Pricing was refreshed from the official
feed and the full CountTokens preflight passed at 00:48:16 UTC with 27,714 tokens.
Only then did the window start: **00:48:16.472–01:03:16.472 UTC on 2026-10-08**.
The actual run stopped at **00:49:49.314 UTC** on the first semantic regression.

## Observed behavior

| Turn | Paid calls | Outcome |
| --- | ---: | --- |
| “Another email for Casey, please.” | 1 | Passed: separate Casey goal and “What would you like to say?”; Alex retained unchanged at version 1. The prior six-call new-goal loop did not recur. |
| “Back to Alex: ask whether the sapphire crate has arrived.” | 2 | Failed: correctly selected Alex, then asked for the already supplied purpose again; no draft returned. |
| Finish Casey / varied transition | 0 | Not attempted after the stop. |
| Calendar preview, revision, closing / control question | 0 | Not attempted after the stop. No Calendar acceptance claim for this batch. |

The first new-goal pass used request `21f4b699-7c92-4a47-b9a2-e81cea3eb069`
at 00:48:16 UTC. On the Alex turn, request
`3d094519-4646-4412-b502-0bf8bcfc07c4` at 00:48:46 UTC supplied the correct owned
goal and purpose but omitted draft text. The tool returned `draft_text_required`,
with the complete validated purpose in its `pending_email_draft` observation.
Request `5a251cde-0c82-4d91-8f82-33e50c9af279` at 00:48:57 UTC supplied the same
goal and a suitable subject/body (asking whether the sapphire crate arrived),
omitting the already retained purpose as allowed by repair guidance. The backend
returned “What would you like to say?” instead of that draft.

## Proven cause and offline reproduction

Every explicit `continue_email_draft` call invokes
`goals.bind_email_continuation` → `select_goal`. The first incomplete call validates
and retains the purpose in the current turn's in-memory goal. The second call
selects the same ID again and reloads the older persisted payload, whose purpose
is empty, overwriting that validated progress before merging the generated draft.
The first rejected call's progress has not yet been committed as a completed turn.

The exact recorded responses reproduce this deterministically without AWS.
`tools/replay_context5_retained_fields.py` instruments selection: immediately before
the second selection, purpose is “ask whether the sapphire crate has arrived”;
immediately afterward, it is empty. Its acceptance assertion fails because the
response contains no `email_draft`. This reproducer is outside default test
discovery and is intentionally RED until the application is fixed.

```sh
THREADLY_TEST_DB=postgresql+asyncpg://threadly@127.0.0.1:55439/threadly_meeting_email_test \
THREADLY_REQUIRE_TEST_DB=1 python -m pytest -s -q tools/replay_context5_retained_fields.py
```

The targeted correction is to retain validated progress when an already owned,
typed goal is rebound within the same current USER turn. A repair should not reload
older values over current progress. Keep source equality, ownership/type checks,
independent goal identity, cross-turn freshness and cancellation boundaries. Add
coverage for same-goal text repair, recipient correction, switching to another
goal, and source/owner rejection. No application correction was made during this
diagnostic; the remaining bug is not claimed fixed.

## Evidence and limits

- [Machine review](context5-model-review.json), [preflights](context5-preflights.json),
  compressed raw ledger `context5-model-ledger.json.gz`, and scenario/state report
  `context5-scenarios.json.gz` retain request IDs, exact synthetic responses and usage.
- `context5-offline-replay-evidence.json.gz` preserves the 24 passing offline budget
  tests, live harness log and failing acceptance replay. The replay's first setup
  attempt lacked fixture imports; that error is retained separately from the actual
  reproduced failure.
- Live pytest reported one passed harness test: that proves guarded execution and
  stopping only, **not behavioral acceptance**. The two-draft scenario failed; other
  cases are unobserved. Three successful provider responses are not three user-task
  successes.
- Casey remained a distinct unchanged pending goal at version 2 during the Alex
  turn. Goal hashes are recorded in the review. No sender/address authority or
  external action was introduced.
- Existing `.4` saved-artifact and initial Calendar-preview evidence remains
  historical. Calendar revision/closing still lacks live acceptance evidence.
