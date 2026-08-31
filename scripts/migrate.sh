#!/usr/bin/env bash
# Apply pending schema migrations to a RUNNING stack's database.
# Fresh databases don't need this (postgres initdb applies every file in
# infra/postgres/init/); this catches existing databases up after a git pull.
set -euo pipefail
cd "$(dirname "$0")/.."

PGUSER="${POSTGRES_USER:-threadly}"
PGDB="${POSTGRES_DB:-threadly}"
psql_run() { docker compose exec -T postgres psql -U "$PGUSER" -d "$PGDB" -v ON_ERROR_STOP=1 "$@"; }

psql_run -c "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());" >/dev/null

for f in infra/postgres/init/*.sql; do
  name="$(basename "$f")"
  applied="$(psql_run -tAc "SELECT 1 FROM schema_migrations WHERE name = '$name'")"
  if [ "$applied" = "1" ]; then
    continue
  fi
  if [ "$name" = "00-baseline.sql" ]; then
    # A database that predates the ledger already has the baseline schema.
    psql_run -c "INSERT INTO schema_migrations (name) VALUES ('00-baseline.sql') ON CONFLICT DO NOTHING" >/dev/null
    echo "recorded existing baseline"
    continue
  fi
  echo "applying $name"
  psql_run -1 -f - < "$f"
  psql_run -c "INSERT INTO schema_migrations (name) VALUES ('$name')" >/dev/null
done
echo "migrations up to date"
