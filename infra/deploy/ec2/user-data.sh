#!/bin/bash
set -Eeuo pipefail
exec > >(tee -a /var/log/threadly-bootstrap.log) 2>&1
trap 'echo "Threadly bootstrap failed at line $LINENO; inspect /var/log/threadly-bootstrap.log"' ERR

# A cost guard is installed before package/network setup. Each boot starts a new timer.
cat >/etc/systemd/system/threadly-autostop.service <<'UNIT'
[Unit]
Description=Stop Threadly staging EC2 after the working session
[Service]
Type=oneshot
ExecStart=/usr/sbin/shutdown -h now
UNIT
cat >/etc/systemd/system/threadly-autostop.timer <<'UNIT'
[Unit]
Description=Threadly staging session limit
[Timer]
OnActiveSec=${AutoStopHours}h
AccuracySec=1min
Unit=threadly-autostop.service
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now threadly-autostop.timer

# Only the exact newly-created, owned data volume may be formatted.
# Nitro exposes EBS volumes as NVMe disks, not necessarily /dev/sdf.
TARGET_SERIAL=$(printf '%s' '${DataVolume}' | tr -d '-')
DATA_DEVICE=''
for attempt in $(seq 1 120); do
  DATA_DEVICE=$(lsblk -dn -o PATH,SERIAL | awk -v serial="$TARGET_SERIAL" '$2 == serial {print $1}')
  [ -n "$DATA_DEVICE" ] && break
  sleep 3
done
[ -n "$DATA_DEVICE" ] && [ -b "$DATA_DEVICE" ]
FSTYPE=$(blkid -s TYPE -o value "$DATA_DEVICE" || true)
if [ -z "$FSTYPE" ]; then
  # Refuse a partitioned disk; this stack creates a blank standalone data volume.
  [ "$(lsblk -nr -o TYPE "$DATA_DEVICE" | wc -l)" -eq 1 ]
  mkfs.ext4 -L threadly-data "$DATA_DEVICE"
else
  [ "$FSTYPE" = ext4 ]
fi
DATA_UUID=$(blkid -s UUID -o value "$DATA_DEVICE")
install -d -m 0755 /srv/threadly-data
if ! grep -q "UUID=$DATA_UUID " /etc/fstab; then
  printf 'UUID=%s /srv/threadly-data ext4 defaults,nofail,x-systemd.device-timeout=60 0 2\n' "$DATA_UUID" >>/etc/fstab
fi
mount /srv/threadly-data
mountpoint -q /srv/threadly-data

export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=3 update
apt-get -o Acquire::Retries=3 install -y ca-certificates curl git python3 unzip
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error --location --retry 3 \
  https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat >/etc/apt/sources.list.d/docker.sources <<SOURCES
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
SOURCES
apt-get -o Acquire::Retries=3 update
apt-get -o Acquire::Retries=3 install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl stop docker docker.socket
install -d -m 0755 /etc/docker /etc/systemd/system/docker.service.d
cat >/etc/docker/daemon.json <<'DOCKER'
{
  "data-root": "/srv/threadly-data/docker",
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"}
}
DOCKER
cat >/etc/systemd/system/docker.service.d/storage.conf <<'UNIT'
[Unit]
RequiresMountsFor=/srv/threadly-data
[Service]
TimeoutStopSec=120
UNIT
systemctl daemon-reload
systemctl enable --now docker

# Official Ubuntu EC2 images normally include the SSM agent snap. Ensure it is running.
if ! snap list amazon-ssm-agent >/dev/null 2>&1; then
  snap install amazon-ssm-agent --classic
fi
snap start --enable amazon-ssm-agent

# Empty deployment workspace and protected local credentials. No Google secrets
# are requested or printed, no application is launched, and no model is invoked.
install -d -m 0755 /opt/threadly
install -d -m 0700 /srv/threadly-data/secrets
python3 - <<'PY'
import base64
import os
import secrets
from pathlib import Path
path = Path('/srv/threadly-data/secrets/threadly.env')
if not path.exists():
    password = secrets.token_hex(24)
    values = {
        'APP_ENV': 'prod',
        'INFERENCE_PROVIDER': 'bedrock',
        'BEDROCK_REGION': '${AWS::Region}',
        'BEDROCK_MODEL_ID': '',
        'BEDROCK_SMALL_MODEL_ID': '',
        'CONVERSATION_ENABLED': 'false',
        'BEDROCK_MAIL_PROCESSING_ACKNOWLEDGED': 'false',
        'GMAIL_SOURCE_MODE': 'on_demand',
        'MAILBOX_BACKGROUND_SYNC_ENABLED': 'false',
        'DOMAIN': '',
        'SECRET_KEY': secrets.token_hex(32),
        'FERNET_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode(),
        'POSTGRES_USER': 'threadly',
        'POSTGRES_PASSWORD': password,
        'POSTGRES_DB': 'threadly',
        'DATABASE_URL': f'postgresql+asyncpg://threadly:{password}@postgres:5432/threadly',
        'CHROMA_URL': 'http://chroma:8000',
        'GOOGLE_CLIENT_ID': '',
        'GOOGLE_CLIENT_SECRET': '',
        'GOOGLE_REDIRECT_URI': '',
    }
    with path.open('x') as handle:
        os.chmod(path, 0o600)
        handle.write('\n'.join(f'{k}={v}' for k, v in values.items()) + '\n')
PY
# No hard-coded checkout of stale or unreviewed application code.
docker version
docker compose version
printf 'Host bootstrap complete. Application configuration/deployment is still pending.\n' >/opt/threadly/BOOTSTRAP_READY
