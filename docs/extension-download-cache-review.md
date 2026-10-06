# Extension download cache correction

## Verified incident — 5 October 2026

The website advertised 0.2.3, but fresh Microsoft Edge downloads contained 0.2.0.
The install page's CTA and the downloaded files' origin metadata both identified
`https://threadly.au/downloads/threadly-extension.zip`; there was no alternate
Edge-specific link. The public version fetch used `cache: "no-store"`, while the
ZIP response had no Cache-Control header.

In the same running Microsoft Edge session, a download with the current checksum
query produced 0.2.3, and the immediately following bare-URL download reproduced
0.2.0. Files were preserved; no extension reload/install, cache deletion or history
deletion was performed.

| Artifact | Manifest | Bytes | SHA-256 |
| --- | --- | --- | --- |
| Recent downloads (6), (7), repeated bare URL (9) | 0.2.0 | 218506 | `8b41dd72c188739036a5151b970ab0932bed2fa07bf113e32d3abd8fa95b683a` |
| Checksum URL in Edge (8), direct current URL | 0.2.3 | 214304 | `404305c1c31f174c91753120e10837899b1eb6fec2fbd9d6bcdc67a70137a6c2` |

The public JSON and release feed matched the second row, frontend commit
`b38a08b79f020cea4067d322a312e168c5eec85e`, run `37312485147`.
These observations identify stale bytes reused at the unchanged browser download
URL, rather than a mislabelled ZIP at the current server or an old-folder assumption.

## Correction

The install button incorporates the validated metadata checksum into its URL.
This bypasses the legacy bare-URL cache and distinguishes same-version rebuilds
and rollbacks. A separate query URL remains available if metadata or JavaScript
is unavailable. Caddy sends no-store for the mutable ZIP and metadata, and
no-cache for the install/home pages and version script. The script reference is
revised to bypass a previously cached script.

The query is a cache identity, not an immutable historical artifact endpoint.
The existing release updater and backend/API behavior are unchanged.

## Validation

From `website-src/`:

- `npm test`: 7 isolated Chromium tests passed. Includes a genuinely cached old
  response, correct ZIP bytes after clicking the CTA, rebuild/rollback, missing
  or invalid metadata, timeout and JavaScript-disabled fallback.
- `python3 tests/check-download-headers.py`: 8 checks passed against the actual
  Caddy handlers in an ephemeral `caddy:2.11.4` container, including both query
  forms, no-store/revalidation/security headers and an unrelated-path 404.
- `npm run build`: passed.

`node --check website/extension-version.js`, Python compilation and
`git diff --check` also passed. `website-ci` repeats browser/build/Caddy checks.

## Production boundary

This is a source correction; it does not change the live website by itself.
After review and explicit production approval, deploy only the three changed
website assets and `infra/caddy/Caddyfile.threadly-au`, validate and reload Caddy,
and refresh the install page. Preserve the running backend release and served
extension ZIP; the full backend deployment script is unnecessary for this scope.
Verify the public headers and CTA-fetched ZIP checksum in Edge after rollout.
Existing unpacked installations still require their normal update/reload process.
