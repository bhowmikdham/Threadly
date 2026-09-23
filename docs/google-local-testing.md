# Google OAuth on domain-free EC2 staging

This is a manual test client, not the extension's login UI. It uses the existing
backend begin/exchange/reconnect endpoints, state, S256 PKCE, account binding and
encrypted Google token storage. No database migration or new public API is added.
The laptop helper needs only Python 3.10+; the API remains behind an SSM tunnel.

## What to register in Google Cloud

Use the team's **Web application** OAuth client. Enable Gmail API and Google
Calendar API in the same Google Cloud project. Configure External / Testing and
add each intended test account under Audience. Do not create a Chrome Extension
client for this backend authorization-code exchange.

Add exactly this **Authorized redirect URI**, with no trailing slash:

```text
http://127.0.0.1:8765/oauth/callback
```

Leave Authorized JavaScript origins empty for this CLI test. The browser callback
is received on the laptop, not EC2. Google allows loopback HTTP redirect URIs for
testing; the application's new exception is narrower than Google's rule. The old
PR #37 release rejects this callback, even if listed in Google Cloud: merge and
deploy the local-callback release before attempting login.

If configuring Data Access manually, the existing backend requests:

- Initial login: `openid`, `email`, `profile`, `https://www.googleapis.com/auth/gmail.readonly`.
- Explicit Calendar reconnect: also `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
  and `https://www.googleapis.com/auth/calendar.events.freebusy`.

Do not add write scopes for this read-only setup. Requested scopes are not proof
of granted permissions; inspect the capability response after consent.

## Backend security boundary

`GOOGLE_ALLOW_LOOPBACK_TEST_CALLBACK` defaults to `false`. When true, **only**
`http://127.0.0.1:8765/oauth/callback` may use HTTP, and that complete string must
also be in `GOOGLE_REDIRECT_URI` or `GOOGLE_REDIRECT_URI_ALLOWLIST`. Other hosts,
ports, paths, query strings, fragments and credentials remain rejected for HTTP.
HTTPS callbacks continue to require exact allowlisting. Both begin and exchange
validate this rule; disabling it invalidates pending local sign-ins.

This setting does not change `APP_ENV=prod`, weaken authentication, enable Google
writes, open EC2 ports or bypass PKCE/state/replay checks. Return the flag to false
when retiring local testing. Public production callbacks still need HTTPS.

## EC2: configure after deploying the merged release

Use **EC2 Session Manager**, not CloudShell. First deploy the reviewed merged
commit using the pinned deployment procedure. Confirm the recorded release
contains `infra/deploy/ec2/configure-google.py` before running:

```bash
sudo python3 /opt/threadly/current/infra/deploy/ec2/configure-google.py \
  --client-id YOUR_WEB_APPLICATION_CLIENT_ID
```

Replace the client ID with your actual ID (it is not a secret). Paste the secret
at the hidden prompt, never as a command argument, in chat or into Git. This
interactive root-only script keeps a private configuration backup, preserves
Bedrock/database/encryption settings and existing redirect allowlist entries,
sets the fixed callback and its opt-in flag, and keeps both write flags false.
Google secrets with unexpected dotenv-special characters are refused without
modifying settings. No provider request or service restart occurs yet.

Reload API and every worker using the **currently deployed** release. Run this as
a separate command; the script is passed with `-c` and unused stdin is closed so
Compose cannot consume the remaining pasted commands:

```bash
sudo bash -c '
set -euo pipefail
exec 9>/var/lock/threadly-deploy.lock
flock -n 9 || { echo "Another deployment is running."; exit 1; }
export THREADLY_RELEASE=$(cat /srv/threadly-data/deployment/current-commit)
[[ "$THREADLY_RELEASE" =~ ^[0-9a-f]{40}$ ]]
export THREADLY_ENV_FILE=/srv/threadly-data/secrets/threadly.env
RELEASE_DIR=/opt/threadly/releases/$THREADLY_RELEASE
COMPOSE=(docker compose --project-name threadly --env-file "$THREADLY_ENV_FILE" -f "$RELEASE_DIR/infra/deploy/ec2/compose.staging.yml")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" run --rm --no-deps -T -v "$RELEASE_DIR/infra/deploy/ec2/preflight.py:/code/preflight.py:ro" api python /code/preflight.py
"${COMPOSE[@]}" up -d --no-build --no-deps --force-recreate --wait --wait-timeout 240 api assistant-worker action-worker sync-worker
curl --fail --silent --show-error http://127.0.0.1:8000/readyz
' </dev/null
```

Existing queued work may resume when workers restart. A healthy API proves local
configuration/readiness, not successful Google consent.

## Laptop: tunnel and sign in

With an AWS CLI profile authorized for SSM and the Session Manager plugin installed,
open this tunnel in one laptop terminal and leave it running:

```bash
aws ssm start-session --region ap-southeast-2 \
  --target i-09a783f8a5b22df7f \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8000"],"localPortNumber":["8000"]}'
```

Use a checkout of the same reviewed release on the laptop (or download the helper
from that exact commit and verify its SHA-256). In a second laptop terminal, from
the checkout root:

```bash
python3 backend/tools/google_local_login.py
```

The helper binds **127.0.0.1:8765** before beginning consent and opens Google's
login page. If the browser does not open, use the authorization URL printed in
your terminal; do not share it. The callback's state is checked locally, then the
original verifier/state/code are sent through the tunnel to the backend. The
Google client secret and refresh token never go to the helper.

Success prints `GOOGLE_LOGIN_OK` and capability readiness. The Threadly session
JWT is saved only in `~/.threadly-staging/session.json` (600; directory 700), never
printed. Treat this file as a credential; keep it off Git and do not share it.
A false `gmail_read` means the account still needs the requested grant even if
identity login succeeded. No email content, Calendar slots or model output has
been tested by this login helper.

Then deliberately add Calendar read access to the **same saved account**:

```bash
python3 backend/tools/google_local_login.py --calendar
```

Expected capability checks after full read consent: `gmail_read=True`,
`calendar_read=True`, `gmail_send=False`, `calendar_write=False`. Partial grants
remain partial. The helper never requests sending/booking permission and performs
no mailbox sync, model request, send or booking. Renew an expired Threadly session
by running ordinary login again before Calendar reconnect.

## Failure handling and completion evidence

- `invalid_redirect_uri` / begin HTTP 400: verify the **new release**, opt-in flag
  and identical callback in backend and Google. Do not bypass the validator.
- `redirect_uri_mismatch`: Google client registration differs (host, port, path,
  scheme and trailing slash all matter).
- Access blocked: verify test-user membership and any Workspace administrator or
  project restrictions; do not change grants to work around an account restriction.
- Callback listener busy: close the earlier test helper; do not register a new port.
- Google denial/timeout: restart login; states expire and are not reused.
- Exchange HTTP error/timeout: do not resubmit the old code/state. Restart login.
- Helper capability read failure after `GOOGLE_LOGIN_OK`: the session is already
  saved; diagnose connectivity without sharing its JWT.
- External apps in Google's Testing status requesting Gmail/Calendar access may
  need consent again after seven days when their refresh token expires.

Record deployed SHA, successful identity login and **actual** capability values.
Gmail/Calendar live read comparisons and all controlled-write recovery tests remain
separate gates. Delete the local session file when no longer needed; deletion does
not revoke Google's grant. The existing backend disconnect clears server-side
usable credentials; Google account consent revocation is a separate operation.

Sources: [Google web-server OAuth and localhost exception](https://developers.google.com/identity/protocols/oauth2/web-server),
[Google token expiration](https://developers.google.com/identity/protocols/oauth2#expiration),
[AWS SSM port forwarding](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-start.html#sessions-start-port-forwarding).
