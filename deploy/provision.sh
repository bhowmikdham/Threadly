#!/usr/bin/env bash
# Day-0 AWS provisioning: one command from your laptop to a running Threadly box.
# Creates (idempotently, by Name tag): security group (443+22), EC2 t3.small
# (Ubuntu 24.04, 30GB gp3), Elastic IP — then bootstraps the stack via user-data.
#
# Requires: aws CLI v2 configured with credentials, an existing EC2 key pair.
#
# Usage:
#   KEY_NAME=my-keypair ./deploy/provision.sh
# Options (env vars):
#   AWS_REGION      default ap-southeast-2
#   INSTANCE_TYPE   default t3.small
#   KEY_NAME        required — existing EC2 key pair name
#   DOMAIN          optional — baked into .env for Caddy auto-TLS
#   REPO_URL        default this repo's public URL (private repo: use a
#                   tokenized https URL or set up a deploy key first)
#   BRANCH          default main
#   SSH_CIDR        default 0.0.0.0/0 — tighten to <your-ip>/32
set -euo pipefail

REGION="${AWS_REGION:-ap-southeast-2}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"
KEY_NAME="${KEY_NAME:?set KEY_NAME to an existing EC2 key pair name}"
DOMAIN="${DOMAIN:-}"
REPO_URL="${REPO_URL:-https://github.com/bhowmikdham/Threadly.git}"
BRANCH="${BRANCH:-main}"
SSH_CIDR="${SSH_CIDR:-0.0.0.0/0}"
NAME=threadly

aws() { command aws --region "$REGION" "$@"; }

echo "== VPC"
VPC_ID=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
[ "$VPC_ID" != "None" ] || { echo "no default VPC in $REGION"; exit 1; }

echo "== security group (443 + 22 only)"
SG_ID=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$NAME-sg Name=vpc-id,Values="$VPC_ID" \
        --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG_ID" = "None" ]; then
  SG_ID=$(aws ec2 create-security-group --group-name $NAME-sg --description "Threadly: 443+22 only" \
          --vpc-id "$VPC_ID" --query GroupId --output text)
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port 22 --cidr "$SSH_CIDR" >/dev/null
fi
echo "   $SG_ID"

echo "== existing instance?"
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters Name=tag:Name,Values=$NAME Name=instance-state-name,Values=pending,running,stopped \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)

if [ "$INSTANCE_ID" = "None" ]; then
  echo "== launching $INSTANCE_TYPE"
  AMI_ID=$(aws ssm get-parameter \
    --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
    --query Parameter.Value --output text)
  USERDATA=$(mktemp)
  {
    echo "#!/bin/bash"
    echo "export THREADLY_REPO_URL='$REPO_URL'"
    echo "export THREADLY_BRANCH='$BRANCH'"
    echo "export THREADLY_DOMAIN='$DOMAIN'"
    tail -n +2 "$(dirname "$0")/bootstrap.sh"
  } > "$USERDATA"
  INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}' \
    --user-data "file://$USERDATA" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text)
  rm -f "$USERDATA"
  echo "   $INSTANCE_ID — waiting for running state"
  aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
else
  echo "   reusing $INSTANCE_ID"
fi

echo "== elastic IP"
ALLOC_ID=$(aws ec2 describe-addresses --filters Name=tag:Name,Values=$NAME-eip \
           --query 'Addresses[0].AllocationId' --output text)
if [ "$ALLOC_ID" = "None" ]; then
  ALLOC_ID=$(aws ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$NAME-eip}]" \
    --query AllocationId --output text)
fi
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$ALLOC_ID" --allow-reassociation >/dev/null
PUBLIC_IP=$(aws ec2 describe-addresses --allocation-ids "$ALLOC_ID" --query 'Addresses[0].PublicIp' --output text)

cat <<DONE

Provisioned.
  instance : $INSTANCE_ID ($INSTANCE_TYPE, $REGION)
  public IP: $PUBLIC_IP   (Elastic — survives restarts)
  ssh      : ssh ubuntu@$PUBLIC_IP

Bootstrap runs automatically via user-data (~3-5 min):
  watch it : ssh ubuntu@$PUBLIC_IP 'sudo tail -f /var/log/cloud-init-output.log'

Then finish by hand (once):
  1. DNS A record: ${DOMAIN:-<your-domain>} -> $PUBLIC_IP
  2. ssh in, edit /opt/threadly/.env (Google OAuth, ElevenLabs, OpenRouter,
     Mac Ollama tailscale URL), then: cd /opt/threadly && sudo docker compose up -d
  3. Health: curl -s https://${DOMAIN:-$PUBLIC_IP}/healthz
  4. Set the \$20 billing alarm in CloudWatch (billing metrics live in us-east-1).

Day-N updates: ./deploy/update.sh ubuntu@$PUBLIC_IP
DONE
