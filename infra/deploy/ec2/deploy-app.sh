#!/usr/bin/env bash
# Run on the bootstrapped EC2 host: sudo bash deploy-app.sh <full Git commit>
set -Eeuo pipefail
umask 077
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run with sudo on the Threadly EC2 host."
RELEASE="${1:-}"
[[ "$RELEASE" =~ ^[0-9a-f]{40}$ ]] || die "Supply a full 40-character reviewed Git commit."
[[ -f /opt/threadly/BOOTSTRAP_READY ]] || die "Host bootstrap is not complete."
mountpoint -q /srv/threadly-data || die "Persistent data disk is not mounted."
exec 9>/var/lock/threadly-deploy.lock
flock -n 9 || die "Another deployment is running."

ENV_FILE=/srv/threadly-data/secrets/threadly.env
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die "Protected environment file is missing or is a symlink."
[[ "$(stat -c '%u:%a' "$ENV_FILE")" == "0:600" ]] || die "Environment file must be owned by root with mode 600."
REPO=https://github.com/bhowmikdham/Threadly.git
RELEASE_DIR="/opt/threadly/releases/$RELEASE"
install -d -m 0755 /opt/threadly/releases
install -d -m 0700 /srv/threadly-data/backups /srv/threadly-data/deployment
trap 'echo "Deployment failed at line $LINENO. Data volumes are preserved. Inspect the failing stage before retrying." >&2' ERR

if [[ ! -d "$RELEASE_DIR" ]]; then
    (umask 022; git clone "$REPO" "$RELEASE_DIR")
fi
[[ "$(git -C "$RELEASE_DIR" remote get-url origin)" == "$REPO" ]] || die "Unexpected release repository."
[[ -z "$(git -C "$RELEASE_DIR" status --porcelain)" ]] || die "Release checkout has local changes; refusing to overwrite."
git -C "$RELEASE_DIR" fetch origin "$RELEASE"
(umask 022; git -C "$RELEASE_DIR" checkout --detach "$RELEASE")
[[ "$(git -C "$RELEASE_DIR" rev-parse HEAD)" == "$RELEASE" ]] || die "Commit verification failed."

export THREADLY_RELEASE="$RELEASE"
export THREADLY_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose --parallel 1 --project-name threadly --env-file "$ENV_FILE" -f "$RELEASE_DIR/infra/deploy/ec2/compose.staging.yml")
"${COMPOSE[@]}" config --quiet
printf '[1/5] Building the reviewed backend release.\n'
"${COMPOSE[@]}" build api
"${COMPOSE[@]}" run --rm --no-deps -T -v "$RELEASE_DIR/infra/deploy/ec2/preflight.py:/code/preflight.py:ro" api python /code/preflight.py

# Pull only missing dependencies. Record actual image IDs after deployment.
# Avoid upgrading PostgreSQL/Chroma implicitly on every application release.
for IMAGE in postgres:16 chromadb/chroma:latest; do
    docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
done
printf '[2/5] Starting PostgreSQL and saving a backup before schema changes.\n'
"${COMPOSE[@]}" up -d --wait --wait-timeout 120 postgres
"${COMPOSE[@]}" stop -t 150 api assistant-worker action-worker sync-worker
BACKUP="/srv/threadly-data/backups/predeploy-$(date -u +%Y%m%dT%H%M%SZ)-$RELEASE.sql.gz"
"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$BACKUP.partial"
gzip -t "$BACKUP.partial"
mv "$BACKUP.partial" "$BACKUP"

printf '[3/5] Applying database migrations before starting application processes.\n'
"${COMPOSE[@]}" run --rm --no-deps -T api alembic upgrade head
"${COMPOSE[@]}" run --rm --no-deps -T api alembic current

printf '[4/5] Starting API, Chroma, assistant and action workers (on-demand Gmail).\n'
"${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 240 api assistant-worker action-worker
curl --fail --silent --show-error http://127.0.0.1:8000/healthz
curl --fail --silent --show-error http://127.0.0.1:8000/readyz
for WORKER_SERVICE in assistant-worker action-worker; do
    WORKER_ID=$("${COMPOSE[@]}" ps -q "$WORKER_SERVICE")
    [[ -n "$WORKER_ID" ]] || die "$WORKER_SERVICE container is missing."
    [[ "$(docker inspect --format '{{.State.Running}}' "$WORKER_ID")" == true ]] || die "$WORKER_SERVICE is not running."
done

printf '\n[5/5] Recording the release and resolved images.\n'
"${COMPOSE[@]}" images --format json > "/srv/threadly-data/deployment/$RELEASE-images.json"
printf '%s\n' "$RELEASE" > /srv/threadly-data/deployment/current-commit
ln -sfn "$RELEASE_DIR" /opt/threadly/current
"${COMPOSE[@]}" ps
printf '\nDEPLOYMENT_READY commit=%s\n' "$RELEASE"
printf 'API: http://127.0.0.1:8000 on the server (use an SSM tunnel from your laptop).\n'
printf 'GitHub source: %s\nRelease checkout: %s\nPre-migration backup: %s\n' "$REPO" "$RELEASE_DIR" "$BACKUP"
printf 'Google OAuth and Bedrock generation require their separate configuration and smoke tests.\n'
printf 'This is a pinned deployment, not automatic GitHub push-to-deploy.\n'
