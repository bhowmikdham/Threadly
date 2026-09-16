# Google connection and capability lifecycle — B01

The backend now stores actual grants and verified Google identity, provides an
owned capability snapshot, and handles refresh/disconnect without committing a
caller's pending action transaction. Send and Calendar handlers remain unavailable.
This contract is ready for backend tests; frontend integration is deferred.

## Existing frontend boundary

Inspected `origin/frontend` at `43694c1774e07e67911e55fee6348031904495dc`:
`sidepanel.tsx` uses `chrome.identity.getAuthToken` and calls Google directly.
It does not call the backend's authorization-code exchange. An access token from
that API must **not** be submitted as an authorization code. No frontend files are
changed here and no end-to-end extension login is claimed.

B01 replaces the old stateless `{code, redirect_uri}` exchange with a versioned
state/PKCE handshake. Existing API clients must adopt the fields below; old payloads
return 422. This is an intentional auth-contract change, not an invisible fallback.
Do not roll it out to an existing code-exchange client before updating that client.
Google OAuth is not configured on the last confirmed staging deployment.

## Endpoint mapping

| Endpoint | Authentication and request | Result |
|---|---|---|
| `POST /auth/google/begin` | No JWT; `redirect_uri`, S256 `code_challenge` | One-use `state`, `authorization_url`, `expires_at` |
| `POST /auth/google/reconnect` | Existing JWT; same request | Same result, bound to the current user and account version |
| `POST /auth/google/exchange` | `code`, `redirect_uri`, returned `state`, original `code_verifier` | Existing `{jwt,user:{id,email,name}}` response |
| `POST /auth/google/disconnect` | Existing JWT; no body | `{connected:false,provider_revocation:"not_requested"}` |
| `GET /assistant/capabilities` | Existing JWT; no user ID input | Owned account, capability list and reconnect contract |
| `POST /auth/refresh` | Existing JWT | Threadly JWT renewal; does not refresh Google grants |

For each login, a future client generates a random RFC 7636 verifier (43–128 valid
characters), keeps it private and sends its base64url SHA-256 challenge to `begin`.
Open the returned Google URL using the configured client/callback mechanism. Verify
the callback's state equals the saved state before posting the code, state and
verifier to `exchange`. Keep the verifier out of URLs/logs and clear it afterwards.
The backend checks it independently; a callback state alone cannot complete login.
Auth responses use `Cache-Control: no-store`.

```mermaid
sequenceDiagram
    participant C as Future client or API test harness
    participant API as Backend
    participant DB as PostgreSQL
    participant G as Google
    C->>C: Generate and retain random verifier
    C->>API: Begin / reconnect with S256 challenge and exact callback
    API->>DB: Store hashed state, challenge, expiry and optional owner/version
    API-->>C: State and authorization URL
    C->>G: User consent with state and S256 challenge
    G-->>C: Callback code and state
    C->>C: Check callback state
    C->>API: Code + state + verifier + callback
    API->>DB: Lock and consume matching unexpired state, commit
    API->>G: Exchange code and verifier, then fetch userinfo
    G-->>API: Actual grants, tokens, verified identity
    API->>DB: Check reconnect owner/version; persist encrypted tokens and grants
    API-->>C: Threadly JWT and profile
```

State expires after ten minutes and is consumed **before** network exchange.
Concurrent replay has one winner. Failure/timeout requires a new sign-in attempt;
rolling back user persistence does not resurrect the state. Only the state hash
and challenge are stored, never raw codes, verifier or raw state. Begin removes
at most 100 records expired for more than a day; deployment-wide rate limiting
and scheduled retention remain B19 release work before broad public exposure.

Login requests only OpenID identity and Gmail read access. The URL supports
incremental consent (`include_granted_scopes=true`), but arbitrary scope input is
not accepted. Calendar/send consent will be added deliberately with installed
capabilities. Requested scopes are never persisted as granted scopes.

## Capability response

The typed OpenAPI response is `backend/app/schemas/capabilities.py`. Each entry
has `id`, `implemented`, `enabled`, `scope_status`, `status`, `ready`, `source` and
`any_of_scopes` (alternatives, not an all-scopes requirement).

| Capability | Installed/enabled | Scope interpretation |
|---|---|---|
| `gmail_read` | Yes | Gmail readonly, modify or full mail access |
| `gmail_send` | No | Grants may be known, but readiness is always false |
| `calendar_read` | No | Recognizes freebusy/read grants; actual per-calendar reads still need B12 |
| `calendar_write` | No | Events/calendar grants do not install a booking executor |

`scope_status` is `unknown`, `missing` or `granted`. `ready` additionally needs a
connected, verified account and an access token with a 120-second margin or a
stored refresh token. It is **stored evidence, not a live validity probe**; remote
revocation can remain unknown until the next provider/refresh request. The response
includes that distinction explicitly. A legacy row may be connected but unverified
with unknown grants; reconnect establishes new evidence. No requested-scope default
or inference from an encrypted token enables writes.

Google capability readiness is separate from `/assistant/workflows` implementation
availability and from source selection. A route/classifier cannot use this snapshot
as permission to send. Future B03/B04 callers must bind the account version and
recheck the exact action source/capability before approval/dispatch.

## Token and transaction behavior

- Code exchange fetches tokens/userinfo before locking user records. A reconnect
  checks the existing Google subject and saved account version; mismatch is 409.
  User persistence flushes but does not commit; the API owns its transaction.
- Token retrieval reads fresh committed credential state in a separate short
  session, including on the cached-token path. It does not trust a caller's stale
  ORM user and never flushes or commits that caller's pending data.
- Refresh network calls occur after the credential-read session closes. A separate
  transaction locks the user and compares `google_token_version` before persistence.
  A concurrent reconnect/disconnect/refresh causes the stale result to return 409.
  Competing refreshes may reach Google, but only one can persist; reload and retry
  the credential lookup, never an uncertain external action.
- `google_account_version` changes on login/reconnect, disconnect or scope changes;
  ordinary token renewal increments only `google_token_version`. This avoids
  invalidating an otherwise current action simply because its access token renewed.
- Missing replacement refresh tokens preserve existing encrypted refresh tokens.
  Missing refresh-response scope metadata preserves prior grants; a new login with
  missing scope metadata records unknown grants. Explicit reduced grants replace
  prior grants and advance the account version.
- Refresh `invalid_grant` disconnects locally with version fencing. Transient network,
  malformed response, `invalid_client` and service errors do not erase valid credentials.
  Errors omit provider bodies/tokens. Provider status alone is not revocation proof.
- Disconnect clears local Google tokens. It does not revoke the provider grant,
  delete cached mailbox data, invalidate Threadly JWTs or cancel a request already
  sent to Google. Those are distinct lifecycle actions. Future action workers must
  recheck version/connection immediately before dispatch and reconcile any uncertainty.

Future action code must call token retrieval **outside** task/action/user locks,
then recheck its own source/account preconditions under the normal lock order.
The legacy helper signature remains `(session,user)` for callers, but all credential
reads and writes use their dedicated sessions. No new sender uses it yet.

## Migration and deployment

Migration `f1a2b3c4d5e6` follows merged B02 head `d9302f5b7a14`. The unfinished seed
migration's historical parent was corrected only in this isolated integration branch.
It adds actual-grant/identity/connection/version fields to users and
`google_oauth_sessions`. Legacy encrypted token bytes, mailbox rows, artifacts and
action history are preserved. Existing token holders are marked connected; grants
and email verification stay unknown until verified login.

Downgrade retains legacy token columns but removes new metadata/state, invalidating
pending sign-ins. It refuses while any action history exists because action approval
may depend on account versions. Never delete user action history just to downgrade.

Configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` and/or
comma-separated `GOOGLE_REDIRECT_URI_ALLOWLIST`, plus the existing encryption/session
keys. Redirects must be exact allowlisted HTTPS URIs with no credentials or fragment;
an empty allowlist rejects exchange. These must also match the actual Google OAuth
client. No wildcard callback, invented client type or HTTP server-IP login is implied.

Stop API/workers, back up DB, upgrade schema and start matching code. Controlled
Google consent, callback, grant reduction, reconnect and refresh smoke tests remain
required with the team's configured test account. Frontend wiring is a separate
later gate. Neither local mocks nor healthy EC2 services prove live OAuth works.

## Provider references and evidence

Implementation checked against Google's [server-side OAuth lifecycle](https://developers.google.com/identity/protocols/oauth2/web-server),
[PKCE verifier/challenge specification](https://developers.google.com/identity/protocols/oauth2/native-app#step1-code-verifier),
and Chrome's [identity API](https://developer.chrome.com/docs/extensions/reference/api/identity).
The configured client's live compatibility is still an external gate.
Tests and remaining boundaries: [B01 checkpoint](backend-execution/checkpoints/B01.md).
