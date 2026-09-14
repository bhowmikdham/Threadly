#!/usr/bin/env bash
# Generated cloudshell.sh includes this prefix, stack.json, and launcher-suffix.sh.
set -Eeuo pipefail
export AWS_PAGER=""

REGION="${THREADLY_REGION:-ap-southeast-2}"
STACK="${THREADLY_STACK:-threadly-staging}"
INSTANCE_TYPE="${THREADLY_INSTANCE_TYPE:-t3.large}"
INSTANCE_PROFILE="${THREADLY_INSTANCE_PROFILE:-}"
AUTO_STOP_HOURS="${THREADLY_AUTO_STOP_HOURS:-8}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$REGION" =~ ^[a-z]{2}-[a-z]+-[0-9]+$ ]] || die "Invalid AWS region."
[[ "$STACK" =~ ^[A-Za-z][A-Za-z0-9-]{0,127}$ ]] || die "Invalid stack name."
[[ "$INSTANCE_TYPE" == t3.large || "$INSTANCE_TYPE" == t3.medium ]] || die "Use t3.large or t3.medium."
[[ "$INSTANCE_PROFILE" =~ ^[A-Za-z0-9+=,.@_-]*$ ]] || die "Invalid instance profile name."
[[ "$AUTO_STOP_HOURS" =~ ^([1-9]|1[0-2])$ ]] || die "Auto-stop must be 1 to 12 whole hours."
command -v aws >/dev/null || die "Run this in AWS CloudShell; AWS CLI is required."
command -v python3 >/dev/null || die "Python 3 is required."

WORKDIR=$(mktemp -d "$HOME/threadly-launch.XXXXXX")
chmod 700 "$WORKDIR"
printf 'Region: %s | Stack: %s | Files: %s\n' "$REGION" "$STACK" "$WORKDIR"
aws sts get-caller-identity --region "$REGION" --query '{Account:Account,Identity:Arn}' --output table

# An existing stack is never updated, replaced or started by this launcher.
if aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" --output json > "$WORKDIR/existing.json" 2> "$WORKDIR/describe-error.txt"; then
    python3 - "$WORKDIR/existing.json" <<'PY'
import json, sys
stack = json.load(open(sys.argv[1]))["Stacks"][0]
if not any(t["Key"] == "Project" and t["Value"] == "Threadly" for t in stack.get("Tags", [])):
    sys.exit("Stack name is already used by another project; choose THREADLY_STACK.")
print("Existing stack:", stack["StackStatus"])
for output in stack.get("Outputs", []):
    print(output["OutputKey"] + ": " + output["OutputValue"])
if stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
    sys.exit("Inspect this stack in CloudFormation; it is not ready. No changes made.")
PY
    printf 'No changes made. Use EC2 Start/Stop for this server; do not create another stack.\n'
    exit 0
else
    # Distinguish a missing stack from AccessDenied, expired credentials or network failures.
    python3 - "$WORKDIR/describe-error.txt" "$STACK" <<'PY'
import sys
error = open(sys.argv[1]).read()
if "(ValidationError)" not in error or f"Stack with id {sys.argv[2]} does not exist" not in error:
    sys.exit(error)
PY
fi

if [[ -n "$INSTANCE_PROFILE" ]]; then
    aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE" --region "$REGION" --query InstanceProfile.Arn --output text
    printf 'Reusing profile %s. Its SSM, Bedrock and backup permissions must already be configured.\n' "$INSTANCE_PROFILE"
fi

IMAGE_ID=$(aws ssm get-parameter --region "$REGION" --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id --query Parameter.Value --output text)
aws ec2 describe-images --region "$REGION" --image-ids "$IMAGE_ID" --output json > "$WORKDIR/image.json"
ROOT_DEVICE=$(python3 - "$WORKDIR/image.json" <<'PY'
import json, sys
images = json.load(open(sys.argv[1]))["Images"]
if len(images) != 1:
    sys.exit("Expected exactly one Ubuntu AMI.")
image = images[0]
expected = {"OwnerId": "099720109477", "Architecture": "x86_64", "State": "available",
            "VirtualizationType": "hvm", "RootDeviceType": "ebs"}
if any(image.get(k) != v for k, v in expected.items()):
    sys.exit("AMI verification failed: expected Canonical Ubuntu, x86_64, available, HVM, EBS.")
print(image["RootDeviceName"])
PY
)
python3 - "$WORKDIR/parameters.json" "$IMAGE_ID" "$ROOT_DEVICE" "$INSTANCE_TYPE" "$INSTANCE_PROFILE" "$AUTO_STOP_HOURS" <<'PY'
import json, sys
keys = ["ImageId", "RootDeviceName", "InstanceType", "ExistingInstanceProfile", "AutoStopHours"]
with open(sys.argv[1], "w") as handle:
    json.dump([{"ParameterKey": key, "ParameterValue": value} for key, value in zip(keys, sys.argv[2:])], handle)
PY

cat > "$WORKDIR/stack.json" <<'THREADLY_CLOUDFORMATION'
