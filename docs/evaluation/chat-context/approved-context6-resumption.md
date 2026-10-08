# Approved third-budget resumption (.6)

User approval: **2026-10-08 01:29:42 UTC**, message
`Sentinel_9c1436a73e6c81918da6e5d560589f68`: “yep”, in response to
`Sentinel_422627de23b0819197e726e63797f4af` asking to reopen the 15-minute window
for the nine unused calls while retaining the US$0.60 total cap. Parent delegated
execution with the explicit case caps and the same model/data scope.

This adds time to the third ledger, not a new allowance. Preserve its three
completed email calls and **USD 0.10397772 incl. GST**, the original identity file,
and both exhausted earlier ledgers. Original third-ledger SHA-256:
`7b6f1d8a59c4d92b29fcefc8a6151375d1bcf7dca4c6cd637f772ae6bcb6df40`.

- Application runtime: `4262c5b1f820a7cf0b0eb6e115e4317ad15a768f`.
- Offline evidence: `a1f20a7abdc956f4197db3aee6c2c9b196cc5da0`.
- Release: `contextual-conversation-1.8.9+chat-context.6`.
- Maximum new attempts: **3 email + 6 Calendar**. All repairs, failures and
  auxiliary inference share these counts. Maximum cumulative: **12 attempts**.
- Costs incl. 10% GST: **USD 0.30 per case**, **USD 0.60 cumulative**. Reserve
  USD 0.04961 for every unknown/failed/in-flight attempt. Refreshed public pricing
  retains USD 1.10/M input and USD 5.50/M output, with no caching, extended context,
  guardrails or premium service tier. Full-input CountTokens precedes every call.
- Same Sydney AU Haiku 4.5 profile; only synthetic fixture text and application
  prompt/tool schemas. 32k input / 1,800 output ceilings; no SDK retries; eight
  seconds between inference dispatches; 5s connect / 45s read / 120s USER-turn cap.
- Fresh persisted 15-minute window starts after auth, pricing and first full-input
  preflight. Stop immediately on semantic/state regression, provider failure,
  deadline or cap. Manual result review gates each subsequent USER turn.
- New local DB `threadly_context_eval3_resume_test`; all original diagnostic DBs
  preserved. Rebuild the pre-failure synthetic state with scripted Alex and the
  recorded successful Casey response. No real mail/Calendar writes, publication,
  deployment, settings changes or production cleanup.

The runner and budget guards are in `backend/tools/resume_chat_context6.py`.
The new segment has its own persisted identity; it never rewrites the original
window. Guard tests cover retained prefix, cumulative/case counts and cost,
durable semantic/provider/preflight stops, expiry, missing ledger/window, and
tampered original receipts. Runtime application files remain at the pinned head.

Execution result: stopped on semantic regression at **10/12 cumulative calls,
USD 0.34752047 incl. GST**, with two unused Calendar attempts. The window is closed
by policy; this approval does not authorize another restart. See
[results and evidence](context6-resumption-results.md).
