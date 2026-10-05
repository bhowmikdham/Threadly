#!/usr/bin/env bash
# Called only after the frontend test/build job has succeeded.
set -euo pipefail
die() { echo "$*" >&2; exit 1; }
[[ "$GITHUB_REPOSITORY" == bhowmikdham/Threadly ]] || die 'Unexpected repository.'
[[ "$GITHUB_REF" == refs/heads/frontend ]] || die 'Only frontend can publish.'
[[ "$GITHUB_EVENT_NAME" == push ]] || die 'Only push events can publish.'
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]] || die 'Invalid source commit.'
[[ "$GITHUB_RUN_ID" =~ ^[0-9]+$ ]] || die 'Invalid run ID.'
[[ "$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+$ ]] || die 'Invalid run attempt.'

# The artifact must belong to this run and contain the exact verified bytes.
python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, 'scripts/release')
from download_release import digest, inspect_zip, validate_metadata
release = Path('build/website-release')
meta = validate_metadata(json.loads((release / 'release.json').read_bytes()))
assert meta['commit'] == os.environ['GITHUB_SHA']
assert meta['run_id'] == int(os.environ['GITHUB_RUN_ID'])
assert meta['run_attempt'] == int(os.environ['GITHUB_RUN_ATTEMPT'])
data = (release / 'threadly-extension.zip').read_bytes()
assert digest(data) == meta['sha256'] and len(data) == meta['bytes']
inspect_zip(data, meta['version'])
PY

latest=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/frontend" --jq .object.sha)
if [[ "$latest" != "$GITHUB_SHA" ]]; then
  echo "A newer frontend commit exists; leaving the download unchanged."
  exit 0
fi

tag="extension-download-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
gh release create "$tag" build/website-release/threadly-extension.zip \
  build/website-release/release.json --repo "$GITHUB_REPOSITORY" \
  --target "$GITHUB_SHA" --prerelease --latest=false \
  --title "Website extension build $GITHUB_RUN_ID.$GITHUB_RUN_ATTEMPT" \
  --notes "Tested frontend commit: $GITHUB_SHA. Unpacked website download; not a store release."

# Release creation can take time. Recheck before moving the feed.
latest=$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/frontend" --jq .object.sha)
if [[ "$latest" != "$GITHUB_SHA" ]]; then
  echo "A newer frontend commit exists; keeping the package without promoting it."
  exit 0
fi
if ! gh release view extension-download-current --repo "$GITHUB_REPOSITORY" >/dev/null 2>&1; then
  gh release create extension-download-current --repo "$GITHUB_REPOSITORY" \
    --target "$GITHUB_SHA" --prerelease --latest=false \
    --title "Current website extension download" \
    --notes "Machine-readable feed for the website ZIP. Does not update installed unpacked extensions."
fi
gh release upload extension-download-current build/website-release/release.json \
  --repo "$GITHUB_REPOSITORY" --clobber
{
  echo "Published tested package for commit $GITHUB_SHA."
  echo "The website checks this feed every two minutes. Confirm the server service or public checksum for deployment success."
} >> "$GITHUB_STEP_SUMMARY"
