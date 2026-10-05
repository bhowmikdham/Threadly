# Login persistence — extension 0.2.2

The extension now keeps Threadly credentials in trusted local extension storage,
so reloading the extension or restarting the browser retains login. It renews
expired access with the backend's scoped renewal credential within the original
session deadline (30 days by default). Google credentials remain server-side.

Sign-out and confirmed Google disconnection clear local credentials. Temporary
network/server failures retain them for retry. Session writes are serialized;
delayed refresh or API failures cannot restore a signed-out session or erase a
newer login. Existing conversation state remains in temporary session storage.

An available legacy session is migrated at worker startup. Chrome clears older
session storage on extension updates/reloads, so upgrading from 0.2.1 can require
one fresh sign-in. The update page explains this transition.

Validation on 2026-10-05:

- TypeScript passed; 122 unit tests passed.
- Local production build passed; 21 browser tests passed. The public-only test
  was intentionally skipped in the localhost build, then passed separately with
  PLASMO_PUBLIC_THREADLY_BACKEND_ORIGIN=https://api.threadly.au.
- Seven new browser regressions cover the actual Chrome Extensions Reload
  button, full browser restart, sign-out across another restart, expired access
  renewal without Google, temporary failure versus revocation, server changes,
  and stale API/refresh 401 responses after a newer login.
- Existing concurrent refresh/sign-out and Google disconnect tests passed.
- These browser tests use isolated synthetic accounts; they do not prove a live
  Google consent flow. Backend renewal/revocation tests are in companion PR #85.

Deploy backend PR #85 before merging this frontend release. The frontend branch's
verified release workflow publishes the website ZIP. No extension permission,
manifest identity, Google scope or model changes.
