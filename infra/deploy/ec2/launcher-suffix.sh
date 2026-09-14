THREADLY_CLOUDFORMATION

printf '\nCreating BILLABLE staging resources: %s, 80 GiB gp3, one Elastic IP and a private S3 bucket.\n' "$INSTANCE_TYPE"
printf 'The server stops after %s hours per boot. EBS, the Elastic IP and stored backups still cost money while stopped.\n' "$AUTO_STOP_HOURS"
printf 'AWS credits are not a spending cap. Bedrock usage is separate.\n\n'
aws cloudformation validate-template --region "$REGION" --template-body "file://$WORKDIR/stack.json" --query Description --output text
aws cloudformation create-stack --region "$REGION" --stack-name "$STACK" \
    --template-body "file://$WORKDIR/stack.json" --parameters "file://$WORKDIR/parameters.json" \
    --capabilities CAPABILITY_IAM --enable-termination-protection --on-failure ROLLBACK \
    --tags Key=Project,Value=Threadly Key=Environment,Value=staging --output json

printf 'Waiting for CloudFormation. If this shell disconnects, check the existing stack before rerunning.\n'
if ! aws cloudformation wait stack-create-complete --region "$REGION" --stack-name "$STACK"; then
    aws cloudformation describe-stack-events --region "$REGION" --stack-name "$STACK" \
        --query 'StackEvents[?contains(ResourceStatus, `FAILED`)].[LogicalResourceId,ResourceStatusReason]' --output table || true
    die "Stack did not finish successfully within the wait period. Inspect CloudFormation; do not create a second stack."
fi
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" --query 'Stacks[0].Outputs' --output json > "$WORKDIR/outputs.json"
python3 - "$WORKDIR/outputs.json" <<'PY'
import json, sys
for output in json.load(open(sys.argv[1])):
    print(output["OutputKey"] + ": " + output["OutputValue"])
PY
INSTANCE_ID=$(python3 - "$WORKDIR/outputs.json" <<'PY'
import json, re, sys
value = next(o["OutputValue"] for o in json.load(open(sys.argv[1])) if o["OutputKey"] == "InstanceId")
if not re.fullmatch(r"i-[0-9a-f]+", value):
    sys.exit("Invalid instance ID returned by CloudFormation.")
print(value)
PY
)
printf '\nInfrastructure created. Docker installation may still be running; the Threadly application is NOT deployed yet.\n'
printf 'Connect with the SessionManagerUrl above once the agent is online, then check:\n'
printf '  sudo cloud-init status --wait\n  sudo cat /opt/threadly/BOOTSTRAP_READY\n  sudo docker compose version\n'
printf '\nDaily controls (paste into CloudShell):\n'
printf '  aws ec2 stop-instances --region %s --instance-ids %s\n' "$REGION" "$INSTANCE_ID"
printf '  aws ec2 start-instances --region %s --instance-ids %s\n' "$REGION" "$INSTANCE_ID"
printf '\nTo extend the current work session, run ON THE SERVER:\n  sudo systemctl restart threadly-autostop.timer\n'
printf '\nNext: configure domain, Google OAuth and a supported Bedrock model, then deploy the application.\n'
printf 'Private environment file on server: /srv/threadly-data/secrets/threadly.env (root only).\n'
printf 'The S3 bucket is ready for backups, but no backup job has been installed.\n'
printf 'Keep %s/outputs.json: the data disk and backup bucket are retained if the stack is later deleted.\n' "$WORKDIR"
