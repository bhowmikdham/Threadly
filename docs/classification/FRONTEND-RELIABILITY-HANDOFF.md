# Classification reliability handoff — 8 October 2026

Release status: the corrected backend is deployed and enabled. See the
[8 October rollout and verification record](ROLLOUT-2026-10-08.md). The frontend
cooldown changes below remain work for the frontend team.

## Integration changes

- Keep the existing endpoint, request body, authentication and label mappings.
- Use the returned `valid_until`. New results default to one hour from
  `evaluated_at` instead of five minutes. Do not hardcode a five-minute refresh.
  Continue invalidating immediately on new mail, sends, draft changes, account
  changes and disconnects. Expiry is a maximum display lifetime, not a promise
  that Gmail remained unchanged.
- For **429 `classification_busy`**, honor the actual `Retry-After` header, which
  now varies with remaining capacity. A hardcoded five-second retry is insufficient.
  Pause the client's classification queue for that duration, add small positive
  jitter and resume with at most two concurrent requests. Render each successful
  badge as it arrives. Only enqueue visible threads; deduplicate in-flight requests.
- Handle **429 `gmail_rate_limited`** as a temporary Gmail cooldown, honoring
  `Retry-After`. Do not show a reconnect prompt for this code.
- Handle **503 `gmail_quota_exceeded`** as exhausted Gmail quota, honoring its
  longer `Retry-After` (default one hour). Do not immediately retry or reconnect.
- **503 `classification_provider_unavailable`** remains compatible and now has
  `Retry-After: 5` after bounded backend retries. Retry with bounded backoff only
  while the thread is visible. Permanent configuration failures still need backend
  investigation; never retry forever or invent fallback labels.
- **403 `gmail_access_denied`** remains a genuine or unrecognized access refusal.
  Preserve existing error UX; the backend does not assume every 403 is throttling.

Keep results in view memory and discard obsolete responses using the existing
request-generation/account checks. No persistent browser cache is introduced by
this change; successful responses retain `Cache-Control: no-store`.

## Capacity and frontend scheduling

AWS reports an applied Sydney Haiku 4.5 cross-region quota of **10 requests/minute**
for this account. Backend classification defaults to **8 model attempts/minute**
to leave some headroom for other consumers. Retries count against that budget.
It is a rolling process-local limit, not a distributed account-wide quota manager.
Other assistant traffic can still exhaust shared AWS capacity.

Concurrency remains two until a higher applied quota is verified. A cold page of
22 uncached threads can take over two minutes under the current capacity; merely
serializing requests or increasing concurrency cannot remove that limit. The
one-hour validity cuts repeated refresh demand. New work is rejected before Gmail
reads when the backend is full, reducing retry-driven Gmail traffic.

AWS rejected a requested increase to 120/minute because its increase API compares
against a default quota of 10,000/minute despite returning an applied value of 10.
An AWS support resolution is required before claiming faster inbox throughput.

## Backend behavior

Transient inference failures have at most three total attempts with jittered
backoff, within the existing request timeout. Flow-stream throttles are covered.
Invalid output, changed releases and access-denied failures are not retried.
No model, prompt, Flow version or label semantics changed.

Gmail GET requests retry documented rate-limit 403s, 429s and selected 5xx errors
at most three times. Long cooldowns return immediately to the caller. Mail writes
are unaffected. Fresh source/account/session checks remain in place.
Logs record only allowlisted provider codes/operations and Gmail reasons, never
provider messages, email content or tokens. No database migration or saved
classification data is introduced.

## Findings from frontend PR #126

The reviewed implementation already limits requests to two concurrently in
`contents/inbox-badges.ts`, shares two background slots across Gmail tabs in
`background.ts`, renders successful badges as responses arrive, invalidates when
`lastMessageId` changes and honors the backend's `valid_until`. Keep those behaviors.

The required adjustment is cooldown propagation and scheduling:

1. The HTTP transport should preserve the numeric `Retry-After` header on errors
   (also accept an HTTP-date if its shared implementation supports it).
2. `background.ts` currently returns only `ok`, `code` and `status` on errors.
   Pass a `retryAfterSeconds` value to the content script as well.
3. `contents/inbox-badges.ts` currently retries `classification_busy` after fixed
   5/10/15-second delays. Replace this with the actual server delay plus positive
   jitter. Pause the classification queue across all Gmail tabs in the background
   handler, not only the failed row, so other queued requests do not continue
   hammering the exhausted budget. Keep pending rows unclassified, then resume.
4. Treat `gmail_rate_limited` the same way for that Gmail account. Respect the
   longer `gmail_quota_exceeded` cooldown. Use bounded retries for provider 503s.
5. Recheck account/session, thread generation and visibility before releasing a
   waiting/retry request; drop work for closed/hidden/changed rows. Resolve or clear
   old timers when the account changes. Do not insert stale work into the new queue.
6. Tests should cover a 60-second Retry-After (not just 5), two Gmail tabs, quota
   exhaustion, a hidden row during cooldown, account changes, and successful
   results retained until the returned one-hour expiry.

No new batch endpoint is required: each API request still classifies one thread.
Two in-flight requests plus a shared cooldown provide progressive loading under
the present quota. A quota rejection should remain a pending cooldown rather than
being converted to a five-minute failed-row state after three rapid retries.

PR #126 also uses `chrome.storage.session` for temporary cross-tab results. This
is existing frontend behavior, not new backend storage or a relaxation of the
HTTP `no-store` policy. Extend no retention beyond the returned validity and
preserve account/logout invalidation.
