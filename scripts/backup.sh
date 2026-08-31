#!/usr/bin/env bash
# Nightly postgres dump; keeps the 7 most recent. Chroma is derived data
# (re-embeddable from postgres), so pgdata is the only thing that must survive.
set -euo pipefail
cd "$(dirname "$0")/.."

PGUSER="${POSTGRES_USER:-threadly}"
PGDB="${POSTGRES_DB:-threadly}"
mkdir -p backups
stamp="$(date +%F-%H%M)"
docker compose exec -T postgres pg_dump -U "$PGUSER" "$PGDB" | gzip > "backups/threadly-$stamp.sql.gz"
ls -1t backups/threadly-*.sql.gz | tail -n +8 | xargs -r rm --
echo "backup written: backups/threadly-$stamp.sql.gz"
