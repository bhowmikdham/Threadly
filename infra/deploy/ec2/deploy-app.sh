#!/usr/bin/env bash
# Run on the bootstrapped EC2 host: sudo bash deploy-app.sh <full Git commit> [--public-https|--public-launch]
set -Eeuo pipefail
umask 077
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run with sudo on the Threadly EC2 host."
[[ $# -ge 1 && $# -le 2 ]] || die "Usage: deploy-app.sh <full Git commit> [--public-https|--public-launch]."
RELEASE="$1"
[[ "$RELEASE" =~ ^[0-9a-f]{40}$ ]] || die "Supply a full 40-character reviewed Git commit."
DEPLOY_MODE=private
if [[ $# -eq 2 ]]; then
    case "$2" in
        --public-https) DEPLOY_MODE=public-https ;;
        --public-launch) DEPLOY_MODE=public-launch ;;
        *) die "The only optional modes are --public-https and --public-launch." ;;
    esac
fi
[[ -f /opt/threadly/BOOTSTRAP_READY ]] || die "Host bootstrap is not complete."
mountpoint -q /srv/threadly-data || die "Persistent data disk is not mounted."
exec 9>/var/lock/threadly-deploy.lock
flock -n 9 || die "Another deployment is running."

ENV_FILE=/srv/threadly-data/secrets/threadly.env
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die "Protected environment file is missing or is a symlink."
[[ "$(stat -c '%u:%a' "$ENV_FILE")" == "0:600" ]] || die "Environment file must be owned by root with mode 600."
DOMAIN=''
THREADLY_TRUSTED_PROXY_IP=''
if [[ "$DEPLOY_MODE" != private ]]; then
    # Read DOMAIN as data, never source the protected file as shell code.
    DOMAIN_VALUES=()
    while IFS= read -r LINE; do
        [[ "$LINE" == DOMAIN=* ]] && DOMAIN_VALUES+=("${LINE#DOMAIN=}")
    done < "$ENV_FILE"
    [[ ${#DOMAIN_VALUES[@]} -eq 1 ]] || die "Public HTTPS requires exactly one DOMAIN entry."
    DOMAIN="${DOMAIN_VALUES[0]}"
    [[ ${#DOMAIN} -le 253 ]] || die "DOMAIN is too long."
    [[ "$DOMAIN" != .* && "$DOMAIN" != *. && "$DOMAIN" != *..* ]] \
        || die "DOMAIN must be an unquoted public DNS name."
    IFS='.' read -r -a DOMAIN_LABELS <<< "$DOMAIN"
    [[ ${#DOMAIN_LABELS[@]} -ge 2 ]] || die "DOMAIN must be an unquoted public DNS name."
    for LABEL in "${DOMAIN_LABELS[@]}"; do
        [[ ${#LABEL} -le 63 && "$LABEL" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$ ]] \
            || die "DOMAIN must be an unquoted public DNS name."
    done
    LAST_LABEL="${DOMAIN_LABELS[$((${#DOMAIN_LABELS[@]} - 1))]}"
    [[ "$LAST_LABEL" =~ [A-Za-z] ]] || die "DOMAIN must end in a DNS name, not an IP address."
    if [[ "$DEPLOY_MODE" == public-launch ]]; then
        [[ "$DOMAIN" == api.threadly.au ]] || die "Public launch requires DOMAIN=api.threadly.au."
        [[ -s /srv/threadly-data/public-downloads/threadly-extension.zip ]] \
            || die "Install the verified public extension bundle before public launch."
    fi
    THREADLY_TRUSTED_PROXY_IP=172.30.247.2
fi
export DOMAIN THREADLY_TRUSTED_PROXY_IP
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

if [[ "$DEPLOY_MODE" == public-launch ]]; then
    for PAGE in index.html install/index.html privacy/index.html terms/index.html; do
        [[ -s "$RELEASE_DIR/website/$PAGE" ]] || die "Public site page is missing: $PAGE."
    done
    if grep -rq '__LAUNCH_' "$RELEASE_DIR/website"; then
        die "Public site owner/contact/policy details are unfinished; complete the launch draft first."
    fi
fi

export THREADLY_RELEASE="$RELEASE"
export THREADLY_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose --parallel 1 --project-name threadly --env-file "$ENV_FILE" -f "$RELEASE_DIR/infra/deploy/ec2/compose.staging.yml")
PUBLIC_COMPOSE=("${COMPOSE[@]}" -f "$RELEASE_DIR/infra/deploy/ec2/compose.public-https.yml")
if [[ "$DEPLOY_MODE" == public-launch ]]; then
    PUBLIC_COMPOSE+=(-f "$RELEASE_DIR/infra/deploy/ec2/compose.public-launch.yml")
fi
APP_COMPOSE=("${COMPOSE[@]}")
"${COMPOSE[@]}" config --quiet
if [[ "$DEPLOY_MODE" != private ]]; then
    "${PUBLIC_COMPOSE[@]}" config --quiet
    python3 "$RELEASE_DIR/infra/deploy/ec2/public-network-preflight.py"
    APP_COMPOSE=("${PUBLIC_COMPOSE[@]}")
fi
printf '[1/5] Building the reviewed backend release.\n'
"${APP_COMPOSE[@]}" build api
# One-off checks/migrations use only the private network. The long-running API
# owns its fixed ingress address, so these containers must not join ingress.
"${COMPOSE[@]}" run --rm --no-deps -T -v "$RELEASE_DIR/infra/deploy/ec2/preflight.py:/code/preflight.py:ro" api python /code/preflight.py

# Pull only missing dependencies. Record actual image IDs after deployment.
# Avoid upgrading PostgreSQL/Chroma implicitly on every application release.
IMAGES=(postgres:16 chromadb/chroma:latest)
if [[ "$DEPLOY_MODE" != private ]]; then IMAGES+=(caddy:2.11.4); fi
for IMAGE in "${IMAGES[@]}"; do
    docker image inspect "$IMAGE" >/dev/null 2>&1 || docker pull "$IMAGE"
done
printf '[2/5] Starting PostgreSQL and saving a backup before schema changes.\n'
"${COMPOSE[@]}" up -d --wait --wait-timeout 120 postgres
"${PUBLIC_COMPOSE[@]}" stop -t 30 caddy
"${APP_COMPOSE[@]}" stop -t 150 api assistant-worker action-worker sync-worker
BACKUP="/srv/threadly-data/backups/predeploy-$(date -u +%Y%m%dT%H%M%SZ)-$RELEASE.sql.gz"
"${COMPOSE[@]}" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$BACKUP.partial"
gzip -t "$BACKUP.partial"
mv "$BACKUP.partial" "$BACKUP"

printf '[3/5] Applying database migrations before starting application processes.\n'
"${COMPOSE[@]}" run --rm --no-deps -T api alembic upgrade head
"${COMPOSE[@]}" run --rm --no-deps -T api alembic current

printf '[4/5] Starting API, Chroma, assistant and action workers (on-demand Gmail).\n'
"${APP_COMPOSE[@]}" up -d --no-build --wait --wait-timeout 240 api assistant-worker action-worker
curl --fail --silent --show-error http://127.0.0.1:8000/healthz
curl --fail --silent --show-error http://127.0.0.1:8000/readyz
for WORKER_SERVICE in assistant-worker action-worker; do
    WORKER_ID=$("${APP_COMPOSE[@]}" ps -q "$WORKER_SERVICE")
    [[ -n "$WORKER_ID" ]] || die "$WORKER_SERVICE container is missing."
    [[ "$(docker inspect --format '{{.State.Running}}' "$WORKER_ID")" == true ]] || die "$WORKER_SERVICE is not running."
done
if [[ "$DEPLOY_MODE" != private ]]; then
    printf 'Starting opt-in Caddy on 443 and checking its public certificate locally.\n'
    "${PUBLIC_COMPOSE[@]}" up -d --no-build --no-deps --force-recreate --wait --wait-timeout 120 caddy
    if ! curl --fail --silent --show-error --retry 18 --retry-delay 5 \
        --retry-all-errors --max-time 10 --noproxy '*' --resolve "$DOMAIN:443:127.0.0.1" \
        "https://$DOMAIN/healthz" >/dev/null; then
        "${PUBLIC_COMPOSE[@]}" stop -t 30 caddy
        die "Caddy did not serve certificate-verified HTTPS; public ingress was stopped."
    fi
    if [[ "$DEPLOY_MODE" == public-launch ]]; then
        for SITE_HOST in threadly.au www.threadly.au; do
            if ! curl --fail --silent --show-error --retry 18 --retry-delay 5 \
                --retry-all-errors --max-time 10 --noproxy '*' \
                --resolve "$SITE_HOST:443:127.0.0.1" "https://$SITE_HOST/" >/dev/null; then
                "${PUBLIC_COMPOSE[@]}" stop -t 30 caddy
                die "Public site TLS failed; public ingress was stopped."
            fi
        done
    fi
fi

if [[ "$DEPLOY_MODE" == public-launch ]]; then
    # A public launch must survive the staging eight-hour session limit.
    # Change this only after all application and certificate checks pass.
    systemctl disable --now threadly-autostop.timer
fi

printf '\n[5/5] Recording the release and resolved images.\n'
if [[ "$DEPLOY_MODE" != private ]]; then
    "${PUBLIC_COMPOSE[@]}" images --format json > "/srv/threadly-data/deployment/$RELEASE-images.json"
else
    "${COMPOSE[@]}" images --format json > "/srv/threadly-data/deployment/$RELEASE-images.json"
fi
printf '%s\n' "$RELEASE" > /srv/threadly-data/deployment/current-commit
printf '%s\n' "$DEPLOY_MODE" > /srv/threadly-data/deployment/current-mode
ln -sfn "$RELEASE_DIR" /opt/threadly/current
"${PUBLIC_COMPOSE[@]}" ps
printf '\nDEPLOYMENT_READY commit=%s\n' "$RELEASE"
if [[ "$DEPLOY_MODE" != private ]]; then
    printf 'LOCAL_HTTPS_READY https://%s (verify DNS and reachability from outside EC2).\n' "$DOMAIN"
else
    printf 'PRIVATE_TUNNEL_READY http://127.0.0.1:8000 on the server (use an SSM tunnel).\n'
fi
printf 'GitHub source: %s\nRelease checkout: %s\nPre-migration backup: %s\n' "$REPO" "$RELEASE_DIR" "$BACKUP"
printf 'Google OAuth and Bedrock generation require their separate configuration and smoke tests.\n'
printf 'This is a pinned deployment, not automatic GitHub push-to-deploy.\n'
