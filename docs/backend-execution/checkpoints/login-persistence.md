# Login persistence — backend renewal contract

The extension previously kept its sole credential in temporary browser session
storage. Its refresh endpoint also required a still-valid access token, so it
could not recover after a day of inactivity. The companion frontend change uses
trusted local extension storage and the renewal credential introduced here.

Google exchange and access renewal return a Threadly access/renewal pair. Renewal
credentials are accepted only by refresh and logout. Existing account/session
generations and locked renewal revoke the pair on logout, disconnect or another
Google login. The default 30-day renewal deadline cannot be extended by using the
legacy access-token refresh path; access expiry is capped at the same deadline.

Checks: 73 auth/OAuth tests passed with disposable PostgreSQL 16, zero skips
(22.08 seconds, two local dependency warnings). Full backend Ruff passed. Coverage
includes expired access recovery, revoked/expired/tampered/deleted-account tokens,
endpoint restrictions, no-store responses and fixed-deadline enforcement. Full
exact-head CI is required before deployment; see the PR checks for its result.

No migration, Google scope, model/prompt or AWS resource change. Deploy backend
before publishing the companion extension. Older clients continue using access
JWTs; the new extension saves a scoped renewal token for later browser restarts.
