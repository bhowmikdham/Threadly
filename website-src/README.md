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
