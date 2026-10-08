# Approved .5 email/Calendar diagnostic

Executed and stopped on semantic regression after 3/12 attempts; see
[actual results](context5-results.md). This historical approval does not authorize
an automatic restart/reset after that stop.

User approval: `Sentinel_3973d49c351881918d50d28411bca36c`, 2026-10-08
00:40:11 UTC, “yep you can”, responding to:

> May I run 12 more diagnostic attempts, capped at US$0.60 including GST, to check
> draft switching and Calendar revision/closing? Same AWS Haiku model in Sydney,
> using synthetic conversations and Threadly’s application prompts/tool schemas.
> The run would have a 15-minute window starting after preflight, with no real
> email or event writes.

Application runtime is unchanged from tested backend
`7fb055f409c46479bc1cd1194aa870ff93ccf187`, prompt `.5`; frontend evidence head
`86af12f5e26aca9cb8a9a8c2ba76e1d2cfdd60bf`. The new opt-in harness
`backend/tools/evaluate_chat_context5.py` uses a new ledger and local disposable
database `threadly_context_eval3_test`; both exhausted ledgers and older diagnostic
databases are preserved.

Same model/profile:
`arn:aws:bedrock:ap-southeast-2:710507379899:inference-profile/au.anthropic.claude-haiku-4-5-20251001-v1:0`.
Endpoint Sydney `ap-southeast-2`; AU routing profile. CountTokens uses the verified
bare ID `anthropic.claude-haiku-4-5-20251001-v1:0`. Only internal prompt/tool schemas
and synthetic conversation/tool/model text are transmitted through CountTokens and
Converse. No real Gmail/Calendar data, provider mutations, grants, production
settings, publication or deployment are authorized.

- Six paid attempts each for email and Calendar, twelve total; all auxiliary,
  repair and failed attempts count. No transfer beyond either case cap, no retries.
- Maximum 32,000 counted input / 1,800 output tokens per attempt.
- USD 0.30 per case / 0.60 combined including a 10% GST allowance. Official feed
  rechecked 2026-10-08 00:42:50 UTC (publication 2026-10-07 18:37:33 UTC): unchanged
  USD 1.10/M input and 5.50/M output. Worst reservation is USD 0.29766 per case,
  0.59532 combined including GST. Failed/unknown calls reserve full cost.
- One persisted 15-minute window after preflight; five-second connect and 45-second
  read timeouts, 120-second conversation timeout; at least eight seconds between
  paid attempts. Restarts cannot reset limits or the deadline.
- Every completed user turn pauses for review tied to its recorded payload hash.
  Stop on semantic regression or provider/preflight failure. A stopped/exhausted
  sequence is incomplete, not a pass. No model judge is called.

Email repeats the original new-Casey request, then finishes Alex and Casey. Check
distinct goal IDs, unchanged non-target payloads, correct recipient/purpose and
useful drafts. If calls remain, vary the new request with Morgan and return to Casey.
Calendar repeats Quiet hour tomorrow at 14:00, revises to 15:00, then closes
socially. Preserve title/day/timezone/duration and verify no action approval/job/write.
If calls remain, ask where to confirm and compare the answer to the actual card.

AWS initially returned `LoginRefreshRequired` / “The refresh token has expired.”
The user completed the reopened sign-in; STS verified the expected account at
00:46:03 UTC. No paid attempt or evaluation window started during login recovery.
Actual results, usage and any incomplete coverage must be recorded separately;
approval and a passing harness invocation do not establish behavioral success.
