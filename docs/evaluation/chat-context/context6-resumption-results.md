# .6 live resumption: email passes, Calendar confirmation routing fails

Stopped immediately on the final Calendar turn. **Seven new calls, ten cumulative
third-budget calls; USD 0.34752047 including GST.** Two Calendar calls remain
unused. The semantic-stop rule is in force again; no automatic restart, new
window, publication or deployment is authorized.

Application: `4262c5b1f820a7cf0b0eb6e115e4317ad15a768f`, release
`contextual-conversation-1.8.9+chat-context.6`. Guarded runner:
`2041a84717f80c7b1d930f04994ae4d8cd02b8cf`. Application files did not change.
Frontend remains `86af12f5e26aca9cb8a9a8c2ba76e1d2cfdd60bf`.

## Observed results

| USER turn | Result |
| --- | --- |
| “Back to Alex: ask whether the sapphire crate has arrived.” | Pass, two calls. Useful Alex draft; purpose retained and Casey hash unchanged. The live repair repeated purpose, so the exact omitted-purpose path remains an offline recorded-response proof. |
| “Now Casey's one: thank them for the map.” | Pass, one call. Correct Casey recipient/purpose/text; Alex hash unchanged. All three remaining email calls consumed; optional Morgan variation not attempted. |
| “Put Quiet hour in my diary tomorrow at 2 pm.” | Pass, one call. Correct 2026-10-09 14:00 Australia/Melbourne, explicit saved 30-minute duration, proposed candidate only. |
| “Make it 3 pm instead.” | Pass, one call. Same goal/title/date, 15:00 Melbourne; original candidate superseded, replacement proposed. |
| “Thanks, that's all.” | Pass for the user-facing response: “All right. Take care!” The raw model still offered event guidance; the existing deterministic closing guard replaced it. |
| “Where do I confirm it?” | **Fail, one call.** The model called `review_email_draft(presentation="status")` in a Calendar-only chat. The backend returned Gmail draft-missing and permission controls despite the current proposed Calendar event. |

The final response claimed to retain a drafting request although this chat had no
email goal or draft. It did not render the pending Calendar confirmation card.
This is a semantic review-routing failure, **not another loss of event fields**.
Final state still has the same Calendar goal, 15:00 time and proposed action.

## Proven boundary and hypothesis

Final provider request `581cadf6-a28d-4efc-89e5-15c3a177867f` ran at
**2026-10-08 01:44:44.771–01:44:46.489 UTC**. Its model context contained:

- One focused retained goal, kind `calendar_event`, label `Quiet hour`.
- `pending_calendar_event` revision 2 with `time: 15:00`, date 2026-10-09,
  and current action `674b0d76-2bac-433b-9b06-5cf416751d3c`.
- `pending_email_draft: null`, `active_work: null`.
- Nevertheless, `email_draft_controls` included `Create draft` and
  `Enable draft creation`, with `source: null`.

The model selected the email review tool; `Runtime._call` dispatched it without
a matching email-goal precondition. `email_review.review` found no draft source
and returned its generic missing-draft message, including the unsupported claim
that a drafting request was retained. The Calendar response guard handles
`respond` text; this email-tool terminal result bypassed that path. These are
directly supported by the recorded request/response and code inspection.

**Hypothesis:** always-present email controls may bias the model toward that tool
even when only Calendar work exists. This single run cannot establish that causal
effect or how often it occurs. No provider/auth/token-limit error occurred.

## Targeted next correction and tests (not implemented)

Bind review/status behavior to an owned goal identity and its kind, consistent
with creation/continuation. Reject or repair an email-review call lacking the
intended email goal instead of inventing an email request. Expose applicable
controls for the selected goal, and use the existing fresh owned Calendar action
response for its review status. Ask a normal focused question when multiple goals
make the reference genuinely ambiguous. Avoid fixing only the phrase “confirm it”.

Replay the four recorded Calendar responses through the real conversation/store
path, asserting that the final status resolves the existing Calendar candidate
or returns a typed goal mismatch for bounded repair, never Gmail draft guidance.
Add variants with both email and Calendar goals, explicit return to either,
ambiguous pronouns, and expired/cancelled/current candidate states. Preserve the
existing draft-save receipt, wording-only revision and ownership/source tests.
All these checks can run offline. No new runtime fix or paid probe was made here.

## Budget and isolation evidence

The [approval](approved-context6-resumption.md) reopened only time for the same
ledger. Auth passed at 01:31:47 UTC. Fresh [official AWS price feed](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/bedrockfoundationmodels/USD/current/bedrockfoundationmodels.json)
was checked at 01:36:46 UTC; rates were unchanged. Full-input preflight at
01:39:52 UTC counted **27,937 tokens**, request
`bde8fe06-1a6e-47c9-888e-6f5af276f425`.

The persisted window was **01:39:52.130–01:54:52.130 UTC**. Semantic stop was
recorded at **01:45:27.988 UTC**, and the runner finished at **01:45:28.045 UTC**.
All seven new inference attempts completed. Usage: **196,540 input / 947 output**
tokens; incremental cost **USD 0.24354275 incl. GST**. Cumulative: **280,562 input /
1,329 output**, **USD 0.34752047**. Per case: email **USD 0.20956595**, Calendar
**USD 0.13795452**, below both USD 0.30 limits. Highest new usage was 28,397 input
and 205 output tokens. No hidden/SDK retry or extra model operation ran.

The three original call receipts and token-preflight prefix compare equal; the
original identity bytes and both exhausted prior ledgers are unchanged. Final
third-ledger SHA-256:
`e253f8ab8f47324aa17f0ba2a7e353dc4789fe2ab5b32bfe54f95297c3fb8a0a`.

Only new disposable `threadly_context_eval3_resume_test` was seeded. All earlier
diagnostic databases remain intact. Two mocked Calendar-list GETs occurred;
**zero real Google calls, zero approval records and zero action jobs**. The
superseded and proposed candidate payload hashes remained unchanged after the
wrong review response. No production/schema/settings/release changes occurred.

Budget guard tests at the runner head: **34 passed / one intentional paid-run
skip**. The live harness itself reports `1 passed` because it successfully ran
and stopped; **that is not a semantic acceptance pass**. Application offline
evidence remains 2,322 full tests / 185 focused tests at the pinned runtime.

Raw cumulative requests/responses: `context6-resumption-ledger.json.gz`.
Synthetic state and per-turn output: `context6-resumption-scenarios.json.gz`.
Usage, request IDs, hashes, manual reviews and final scoped DB state:
`context6-resumption-review.json`. Preflights and raw execution logs are adjacent.
