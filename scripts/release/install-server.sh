#!/usr/bin/env bash
# Install on the existing website host from a reviewed checkout of frontend.
set -euo pipefail
die() { echo "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die 'Run with sudo on the website host.'
mountpoint -q /srv/threadly-data || die 'The data volume is not mounted.'
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
public_dir=/srv/threadly-data/public-downloads
[[ -d "$public_dir" && ! -L "$public_dir" ]] || die 'Invalid public download directory.'
[[ -f "$public_dir/threadly-extension.zip" && ! -L "$public_dir/threadly-extension.zip" ]] || die 'Existing public ZIP is missing or a symlink.'
# This directory is dedicated to public downloads, not application or secret files.
if find "$public_dir" -mindepth 1 -maxdepth 1 ! -name threadly-extension.zip ! -name threadly-extension.json -print -quit | grep -q .; then
  echo 'Unexpected entries in public-downloads; inspect before changing ownership.' >&2
  exit 1
fi
if ! id threadly-release >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin threadly-release
fi
systemctl stop threadly-extension-download.timer 2>/dev/null || true
systemctl stop threadly-extension-download.service 2>/dev/null || true
install -d -m 0755 /usr/local/lib/threadly-extension-release
install -m 0644 "$source_dir/download_release.py" /usr/local/lib/threadly-extension-release/
install -d -o threadly-release -g threadly-release -m 0750 /srv/threadly-data/extension-releases
chown threadly-release:threadly-release "$public_dir"
chmod 0755 "$public_dir"
chmod 0644 "$public_dir/threadly-extension.zip"
install -m 0644 "$source_dir/threadly-extension-download.service" /etc/systemd/system/
install -m 0644 "$source_dir/threadly-extension-download.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now threadly-extension-download.timer
echo 'Updater installed. Run systemctl start threadly-extension-download.service after publishing the first feed.'
