# Public website build

Design supplied by the user from their friend’s `index.html` on 1 October 2026.
Original source SHA-256: fa9f8ed7e3d8721fe395efc82e20e153e7fac40a96f858b32fde4da703bf10a7.

The original Download file is unchanged. Preserve its cream/gold palette,
Newsreader/Schibsted typography, inbox demos and light/dark theme. Copy has been
aligned with the released extension; automatic marks, Drive, Contacts and Tasks
are labelled as concepts. All inbox, calendar and voice interactions here use
fictional fixtures, never real Google data or microphone capture.

Brand artwork reuses the extension's existing `Logo` from `components/Icon.tsx`
at commit `b574a11f95ac8fe986450fb5ffcb7fd8e8962b61`: two hooked loops, with
the original paths, 434:184 proportions and stroke retained. The older
`assets/icons/threadly.svg` and `threadly-mark.svg` are different circular-arc
glyphs; do not use them as the brand logo. The favicon uses the same loop paths
on the extension icon's cream background.

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

## Inbox marks concept video

The video beside “The marks” is the user-selected `threadly-classifiers.mp4`:
20 seconds, 1920×1080, 30 fps, H.264 High profile level 4.0, with no audio track.
It animates the same fictional inbox fixtures used by this page. Its caption
retains the distinction between the design concept and released functionality.
The supplied video has not been redrawn or re-encoded. Its original SHA-256 is
`3558fca54c9ab52aab86ce1251fb8182614e40e5431dea6afa6a31143f4909c4`.

The web copy moves the MP4 movie index before its media data for fast startup;
the encoded media bytes remain identical. Web copy SHA-256:
`4af4a20408646bbb016c2ca9899a05cca0006174605d71ae186f66e11a5147af`.
The poster is the opening frame. Native controls and inline playback work without
site JavaScript; `preload="none"` avoids fetching the video before interaction.
The adjacent expandable description supplies the visual information in text.

Playback requires the website CSP to allow same-origin media (`media-src 'self'`).
The original default-deny policy blocks this player. Keep all other directives,
the API configuration, logo assets and separately published extension downloads
unchanged when releasing this scoped addition.
