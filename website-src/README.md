# Public website build

Design supplied by the user from their friend’s `index.html` on 1 October 2026.
Original source SHA-256: fa9f8ed7e3d8721fe395efc82e20e153e7fac40a96f858b32fde4da703bf10a7.

The original Download file is unchanged. Preserve its cream/gold palette,
Newsreader/Schibsted typography, inbox demos and light/dark theme. Copy has been
aligned with the released extension; automatic marks, Drive, Contacts and Tasks
are labelled as concepts. All inbox, calendar and voice interactions here use
fictional fixtures, never real Google data or microphone capture.

`npm ci && npm run build` rebuilds `../website/site.js` from the pinned Motion
release and lockfile. Serve `website/`; this source directory is outside the
public document root. Google Fonts are permitted by the static site's CSP.
Privacy, service information, and support pages describe invited early access.
Public support contact and wider Google signup remain pending; do not claim full
public verification from this hosting rollout. The deploy script rejects unresolved
`__LAUNCH_` markers if a future page introduces them.

`npx playwright install chromium && npm test` verifies the real install page in
isolated Chromium against local fixture ZIPs, including an already-cached old
download, same-version rebuilds, rollback, invalid/unavailable/timed-out metadata,
and JavaScript-disabled downloads. `python3 tests/check-download-headers.py` runs
the actual public Caddy handlers in an ephemeral local `caddy:2.11.4` container.
These checks run in `website-ci`; they do not deploy anything.

The install button uses the public metadata's SHA-256 as a download query string.
The ZIP endpoint remains mutable and sends `Cache-Control: no-store`. The query
bypasses previously cached bare URLs; it does not pin a server-side historical
artifact. Keep the ZIP's no-store policy and the metadata validation together.
After deploying these assets and the Caddy configuration, refresh the install
page before checking the download. Existing unpacked installations are unchanged.
