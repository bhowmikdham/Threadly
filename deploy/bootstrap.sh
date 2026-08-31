#!/bin/bash
# Runs ON the EC2 instance as root (injected as user-data by provision.sh,
# or run manually on a fresh Ubuntu 24.04 box). Idempotent-ish: safe to
# re-run; it won't overwrite an existing .env.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

REPO_URL="${THREADLY_REPO_URL:-https://github.com/bhowmikdham/Threadly.git}"
BRANCH="${THREADLY_BRANCH:-main}"
DOMAIN="${THREADLY_DOMAIN:-}"
APP_DIR=/opt/threadly

apt-get update -y
apt-get install -y git curl make
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh

if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i 's/^THREADLY_DEV_MODE=.*/THREADLY_DEV_MODE=false/' .env
  if [ -n "$DOMAIN" ]; then
    sed -i "s/^THREADLY_DOMAIN=.*/THREADLY_DOMAIN=$DOMAIN/" .env
  fi
  JWT_SECRET="$(openssl rand -hex 32)"
  INTERNAL_TOKEN="$(openssl rand -hex 32)"
  PG_PASSWORD="$(openssl rand -hex 16)"
  FERNET_KEY="$(docker run --rm python:3.12-slim sh -c \
    "pip -q install cryptography >/dev/null 2>&1 && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")"
  sed -i "s/^THREADLY_JWT_SECRET=.*/THREADLY_JWT_SECRET=$JWT_SECRET/" .env
  sed -i "s/^THREADLY_INTERNAL_TOKEN=.*/THREADLY_INTERNAL_TOKEN=$INTERNAL_TOKEN/" .env
  sed -i "s/^THREADLY_FERNET_KEY=.*/THREADLY_FERNET_KEY=$FERNET_KEY/" .env
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$PG_PASSWORD/" .env
  sed -i "s#^THREADLY_DATABASE_URL=.*#THREADLY_DATABASE_URL=postgresql://threadly:$PG_PASSWORD@postgres:5432/threadly#" .env
  chmod 600 .env
fi

docker compose up -d --build

# Nightly backup + weekly image prune (disk on a t3 is finite).
cat > /etc/cron.d/threadly <<'CRON'
15 3 * * * root cd /opt/threadly && ./scripts/backup.sh >> /var/log/threadly-backup.log 2>&1
30 4 * * 0 root docker system prune -af --filter until=168h >> /var/log/threadly-prune.log 2>&1
CRON

echo "bootstrap complete. Remaining manual steps:"
echo "  1. Point your DNS A record at this instance's Elastic IP (Caddy then self-provisions TLS)."
echo "  2. Edit /opt/threadly/.env: GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI, ELEVENLABS_API_KEY,"
echo "     OPENROUTER_API_KEY, THREADLY_OLLAMA_BASE_URL (Mac Tailscale IP) — then:"
echo "     cd /opt/threadly && docker compose up -d"
