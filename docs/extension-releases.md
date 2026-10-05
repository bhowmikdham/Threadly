# Extension distribution and updates

## Current state (0.2.0)

The public API is `https://api.threadly.au`. A store-ready build can be prepared,
but no Chrome Web Store or Edge Add-ons publisher account or listing is configured.
The current website download is an unpacked developer ZIP. Hosting a new ZIP on
AWS does **not** update installed unpacked extensions.

## Automatic website ZIP

Frontend CI now packages the tested public-origin build and publishes a download
feed on successful pushes to `frontend`. The website updater checks this feed
every two minutes after its one-time installation. Pull requests and other
branches cannot publish through this job. Backend and website-only deployments
do not rebuild the extension. See the [setup and rollback guide](extension-download-automation.md).

Every package records the exact source commit and workflow run, even when the
extension version has not changed. Continue increasing the manifest/package
version for user-visible releases and store updates. Existing unpacked installs
still need new files and a browser reload.

## First store release

1. The owner registers a publisher account, accepts the store agreement and handles
   any registration fee. Use a monitored account; do not invent a support inbox.
   [Chrome registration](https://developer.chrome.com/docs/webstore/register).
2. Run **Prepare or submit store release** on the reviewed frontend commit with
   `publish` left false. Download the `threadly-store-package` artifact. Alternatively:
   `PLASMO_PUBLIC_THREADLY_BACKEND_ORIGIN=https://api.threadly.au npm run build`
   then `npm run package`. Do not upload a localhost development build.
3. Upload the ZIP to a new draft listing. Record its actual extension ID. Verify
   `https://<store-extension-id>.chromiumapp.org/oauth/callback` is allowed by both
   the backend and Google OAuth client. The development manifest key is not proof
   that a new store listing will receive that ID. Keep existing supported IDs until
   their users migrate; do not remove callbacks during the transition.
4. Install the draft/test distribution and complete a real Google sign-in, mail read,
   Calendar connection and preferences check. Mock tests do not verify consent.
   Google OAuth public verification/test-user restrictions are a separate gate.
5. Complete the listing with verified owner/support contact, screenshots, privacy
   policy at `https://threadly.au/privacy/`, accurate data-use disclosures and
   permission explanations. Storage holds settings/session state, identity handles
   Google sign-in, sidePanel shows the assistant, activeTab/Gmail host access reads
   the selected Gmail context, and HTTPS API access is requested at sign-in.
   Localhost access remains for the existing developer build and should be declared
   accurately in review; never claim no backend transmission or no stored mail.
6. Submit for review. Only replace the website install/download CTA with a store
   URL after the listing is approved and installation/sign-in are verified.
7. Ask existing unpacked users to switch once to the store version and sign in.
   Do not promise an automatic conversion of developer installs.

## Later releases

- Increase `package.json` and lockfile version, update `tabs/updated.tsx` release
  notes, run typecheck/unit tests and both browser suites, and create a pinned
  public-origin build. Publish new versions to the **same store listing**.
- The browser store distributes updates; timing is browser/store controlled and
  updates may require user attention if permissions change. We do not forcibly
  reload the extension while the user is working.
- `chrome.runtime.onInstalled` opens the bundled `tabs/updated.html` only on a
  numeric version increase. It records the successful notice version locally.
  First installs, browser updates, same-version developer reloads and downgrades
  do not open the page. No remote scripts, tracking parameters or account data are
  used by the update notice.
- After publisher setup, configure Plasmo BPP `SUBMIT_KEYS` as a repository secret
  (never source code), pointing to the existing listing. A maintainer can then
  trigger the release workflow with `publish: true`; preparation defaults to false.
  Publication and store review remain distinct from passing CI.
- To recover a bad release, publish the corrected code with a **higher** version.
  Lower versions will not replace an installed store version.

References: [Chrome distribution](https://developer.chrome.com/docs/extensions/how-to/distribute),
[runtime lifecycle](https://developer.chrome.com/docs/extensions/reference/api/runtime),
[Edge hosting and updates](https://learn.microsoft.com/en-us/microsoft-edge/extensions/publish/hosting-and-updating).

## Verification for 0.2.0

- TypeScript typecheck passed.
- Vitest: 113 tests passed (11 files).
- Built Chromium extension with mock backend: 13 browser tests passed, covering
  connector navigation, preference edits, compiled update page, disconnect failure
  and success, and existing session/conversation behavior.
- Separate pinned-HTTPS build: public-origin browser test passed. This test is
  deliberately skipped in the localhost suite and executed against its own build.
- Screens inspected at narrow panel width and full update-page width. No real
  mail send, event creation, Google revocation or store submission was performed.
