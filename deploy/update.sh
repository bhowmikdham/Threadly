#!/usr/bin/env bash
# Day-N deploy: pull, migrate, rebuild, restart — in that order, remotely.
# Usage: ./deploy/update.sh ubuntu@<elastic-ip-or-domain>
set -euo pipefail
HOST="${1:?usage: deploy/update.sh ubuntu@<host>}"

ssh "$HOST" 'set -e
  cd /opt/threadly
  sudo git pull --ff-only
  sudo docker compose up -d --build
  sudo ./scripts/migrate.sh
  sudo docker compose ps
  curl -sk https://localhost/healthz && echo
'
echo "deployed."
