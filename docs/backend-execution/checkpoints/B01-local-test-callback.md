# B01 follow-up — domain-free Google OAuth test callback

Status: **in_review**. Branch `codex/oauth-local-test`, base PR #37 merge
`01aac67df0e6cbca0b6bb2546c26e7e8d1e5d031`. This closes a setup gap, not the
broader live Google acceptance gate. Original dirty checkout preserved.

## Behavior

- HTTPS remains the default. Explicit `GOOGLE_ALLOW_LOOPBACK_TEST_CALLBACK=true`
  plus exact redirect allowlisting permits only
  `http://127.0.0.1:8765/oauth/callback`. Both begin and exchange validate it.
- A stdlib laptop helper receives this local callback and talks to EC2 through an
  SSM tunnel. The existing state/PKCE/account-bound exchange remains authoritative.
- Login and explicit same-account Calendar read reconnect use existing endpoints;
  no public callback endpoint, fake identity, Google write or mailbox sync is added.
- Helper binds before consent, suppresses callback logging, refuses API redirects
  and proxy environment forwarding, checks state, limits response sizes and waits,
  and saves the Threadly JWT privately (700 directory / 600 file).
- Root-only EC2 setup prompts for the secret without echo, refuses input fallback,
  saves a private backup and atomically updates named settings. Bedrock/encryption
  settings are preserved; external writes and pilot enrollment are disabled.
- No database, model, prompt or Flow change; no new dependencies. The optional
  test client is separate from the deferred extension/frontend integration.

## Verification

- Focused OAuth/local helper/PKCE/token lifecycle tests: **58 passed**, zero skips,
  real disposable PostgreSQL 16 at localhost:55461 (`threadly_oauth_test`).
- EC2 offline suite including settings backup/permissions/injection checks:
  **16 passed**.
- Ruff (app, tests, helper, setup script), Python compilation, CLI help and
  `git diff --check` passed. Both planning documentation validators passed.
- Full backend regression: **1,076 passed / 2 failed**, zero skips. Both failures
  were existing `SimpleNamespace` settings fixtures missing the newly added boolean.
  Updated those fixtures with the real false default; final affected OAuth/Calendar
  regression: **82 passed**, zero skips (17.17 seconds). No production code or test
  assertion was weakened for those failures. Exact-head CI performs the complete
  suite again; its result is recorded in the PR.
- No Google consent, credentials, mailbox data, model invocation or external write
  was used during implementation. Live OAuth still requires merge/deployment,
  client registration/configuration, a laptop SSM tunnel and human consent.

## Rollout / resume

Follow [the complete runbook](../../google-local-testing.md). Register the exact
local callback in the Web application OAuth client; deploy the merged release;
enter the secret directly on EC2 with `configure-google.py`; reload all application
services; run laptop login and explicit Calendar read reconnect. Record actual
capability grants; do not infer them from successful identity login alone.

Keep `APP_ENV=prod` and write flags false. Disabling the local flag immediately
blocks pending local exchanges (existing Threadly JWTs are not revoked). The old
release has no loopback support and cannot use this callback. No migration or
schema downgrade is required to roll back this feature.
