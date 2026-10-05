# Automatic website extension downloads

The stable URL remains
`https://threadly.au/downloads/threadly-extension.zip`.
Successful `frontend` pushes publish a tested package. A small service on the
existing website host checks for that package every two minutes (plus up to ten
seconds of jitter). No AWS credentials or inbound server port are needed in GitHub.
The repository must remain public for the server to download these assets.

## Release path

1. `Frontend integration checks` runs TypeScript checks, unit tests, updater tests,
   the localhost build/browser suite, and a separate build/browser check pinned to
   `https://api.threadly.au`.
2. It ZIPs that exact public build with `manifest.json` at the root. Release metadata
   records the manifest version, commit, workflow run/attempt, byte count and SHA-256.
   The existing development manifest key is preserved for stable unpacked identity.
3. Only a successful **push to `frontend`** runs the publishing job. It verifies the
   artifact against this run, creates an immutable-by-convention prerelease named
   `extension-download-RUN-ATTEMPT`, then updates `release.json` on the
   `extension-download-current` prerelease. These do not become GitHub's latest
   normal release. The generated prereleases and artifacts contain public build
   material only. Never overwrite the per-run assets; rerun CI for a new attempt.
4. The host updater verifies the feed, the current frontend commit, checksum, size,
   ZIP structure, manifest version, required files and public API origin. It
   rechecks the branch after downloading and rejects older workflow runs.
   It also preserves a newer version already published manually, including when
   the deployed package is ahead of the merged frontend branch.
5. It saves the prior ZIP outside the public directory, writes a durable pending
   record, atomically replaces the download, and fetches the public HTTPS URL to
   verify the served checksum. Failure restores the prior ZIP and metadata. An
   interrupted swap is recovered on the next service run before network access.

Unchanged releases fetch only the small feed. GitHub downtime, missing feed,
failed builds or invalid packages leave the existing download in place. A commit
that merges during the final swap may leave the just-tested build live briefly;
the next successful frontend release is picked up by the next poll.

The GitHub job confirms **publication of the feed**, not host deployment. Check the
public hash and the service result to establish that the website has updated.

## One-time server setup

Use the existing AWS Systems Manager access to the website instance
`i-09a783f8a5b22df7f` in `ap-southeast-2`. No application/backend redeploy is needed.
Copy the reviewed `scripts/release/` directory to the host, then run:

```sh
sudo bash scripts/release/install-server.sh
sudo systemctl start threadly-extension-download.service
sudo systemctl status threadly-extension-download.timer --no-pager
sudo journalctl -u threadly-extension-download.service -n 30 --no-pager
```

Install after the workflow has published its first feed; otherwise the service
will report a missing feed and retry on its next timer run. The installer requires
the existing data mount and download, and refuses unexpected contents of the
public download directory. It creates a dedicated `threadly-release` system user,
with write access only to the public download directory and release state.
Root owns the updater code and service definitions. systemd denies writes elsewhere
and access to home directories; the updater never executes downloaded code.

The service is persistent across reboots. No change to the backend deployment
script or Caddy configuration is required: the existing read-only directory mount
sees atomic file replacements. Future **updater script changes** require rerunning
this installer from the reviewed commit; frontend package changes do not.

## Verify a release

On the host, compare `sha256sum` of the public ZIP to
`/srv/threadly-data/extension-releases/current.json`. Independently download the
public URL and compare its SHA-256 to the same run's GitHub `release.json`.
The state directory also contains `previous.zip`, `previous.json`, `update.lock`
and, only during a transaction, `pending.json`.

```sh
curl --fail --location --silent --show-error \
  https://threadly.au/downloads/threadly-extension.zip -o /tmp/threadly-extension.zip
shasum -a 256 /tmp/threadly-extension.zip
```

Inspect service failures with `journalctl`. The timer retries ordinary failures;
it does not send notifications. GitHub Actions reports build/publish failures.
No infrastructure or application checks are skipped silently in the workflow.

## Pause and rollback

Stop both the timer and any active service before a manual rollback:

```sh
sudo systemctl disable --now threadly-extension-download.timer
sudo systemctl stop threadly-extension-download.service
```

Copy `/srv/threadly-data/extension-releases/previous.zip` to a temporary file in
`/srv/threadly-data/public-downloads/`, set mode `0644`, then rename it atomically
to `threadly-extension.zip`. Restore `current.json` from `previous.json` (or remove
it when the previous value is JSON `null`) and remove any `pending.json` after
confirming the previous bytes are restored. Keep the timer disabled until the bad
change is reverted or fixed on `frontend` and a new successful workflow run has
published. Re-enable the timer with `systemctl enable --now` and start the service.

For normal recovery, revert/fix the code through a new `frontend` commit and let
the pipeline publish it. Rerunning an older commit cannot roll back a newer head.
Preserve per-run GitHub releases until no feed/current/rollback record references
them; artifact retention in Actions does not delete those release assets.

## What users receive

New downloads get the latest verified ZIP. People using **Load unpacked** must
download/extract the new files and reload the extension and Gmail tab. Browser
store approval, store publication, automatic installed-extension updates and
real Google consent are separate from this workflow.

## Local verification

```sh
python3 -m unittest discover -s scripts/release -p 'test_*.py' -v
bash -n scripts/release/publish.sh scripts/release/install-server.sh
npm run typecheck
npm test
npm run build
npm run test:e2e
PLASMO_PUBLIC_THREADLY_BACKEND_ORIGIN=https://api.threadly.au npm run build
PLASMO_PUBLIC_THREADLY_BACKEND_ORIGIN=https://api.threadly.au npx playwright test tests/e2e/public-origin.spec.ts
```

Updater tests cover corruption, unsafe archives, size limits, stale commits/runs,
concurrent writers, repeat runs, network/public verification failures and crash
recovery. Browser checks use isolated profiles and fixtures; they do not send mail,
create calendar events or verify live Google consent.
