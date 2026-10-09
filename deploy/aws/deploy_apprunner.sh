#!/usr/bin/env bash
# Run the pushed image on AWS App Runner behind an access token, with the secrets in SSM Parameter Store.
# Run from the repo root with a signed-in AWS CLI and ANTHROPIC_API_KEY in the environment (or in .env):
#   bash deploy/aws/deploy_apprunner.sh
# Creates, once: two SecureString parameters, two IAM roles, a single-instance autoscaling configuration and
# the service. Prints the HTTPS URL; the demo access token is written to ~/.sop-demo-token, not printed.
# Pause when not needed: aws apprunner pause-service --service-arn <arn>   (resume-service to bring it back)
set -euo pipefail
export MSYS_NO_PATHCONV=1
REGION="${AWS_REGION:-us-east-1}"; APP="sop-claims-agent"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
TAGS="Key=Project,Value=$APP"
aws ecr describe-images --repository-name "$APP" --image-ids imageTag=main --region "$REGION" >/dev/null \
  || { echo "no image in ECR; run deploy/aws/cloud_build.sh first"; exit 1; }

KEY="${ANTHROPIC_API_KEY:-$(grep '^ANTHROPIC_API_KEY=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)}"
[ -n "$KEY" ] || { echo "set ANTHROPIC_API_KEY (environment or .env)"; exit 1; }
if [ -f "$HOME/.sop-demo-token" ]; then TOKEN=$(cat "$HOME/.sop-demo-token"); else
  TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(24))")
  (umask 077; printf '%s' "$TOKEN" > "$HOME/.sop-demo-token"); fi  # owner-readable only
aws ssm put-parameter --name "/$APP/anthropic-api-key" --type SecureString --value "$KEY" --overwrite --region "$REGION" >/dev/null
aws ssm put-parameter --name "/$APP/demo-access-token" --type SecureString --value "$TOKEN" --overwrite --region "$REGION" >/dev/null
KEY_ARN="arn:aws:ssm:$REGION:$ACCOUNT:parameter/$APP/anthropic-api-key"
TOKEN_ARN="arn:aws:ssm:$REGION:$ACCOUNT:parameter/$APP/demo-access-token"

ACCESS_ROLE="${APP}-apprunner-ecr-access"; INSTANCE_ROLE="${APP}-apprunner-instance"
TRUST_BUILD='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"build.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
TRUST_TASKS='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"tasks.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam get-role --role-name "$ACCESS_ROLE" >/dev/null 2>&1 || {
  aws iam create-role --role-name "$ACCESS_ROLE" --assume-role-policy-document "$TRUST_BUILD" --tags $TAGS >/dev/null
  aws iam attach-role-policy --role-name "$ACCESS_ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
}
aws iam get-role --role-name "$INSTANCE_ROLE" >/dev/null 2>&1 \
  || aws iam create-role --role-name "$INSTANCE_ROLE" --assume-role-policy-document "$TRUST_TASKS" --tags $TAGS >/dev/null
POLICY="{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ssm:GetParameters\",\"ssm:GetParameter\"],\"Resource\":[\"$KEY_ARN\",\"$TOKEN_ARN\"]},{\"Effect\":\"Allow\",\"Action\":\"kms:Decrypt\",\"Resource\":\"*\",\"Condition\":{\"StringEquals\":{\"kms:ViaService\":\"ssm.$REGION.amazonaws.com\"}}}]}"
aws iam put-role-policy --role-name "$INSTANCE_ROLE" --policy-name read-$APP-parameters --policy-document "$POLICY"
sleep 10  # IAM propagation

ASC_ARN=$(aws apprunner list-auto-scaling-configurations --auto-scaling-configuration-name "$APP-single" --region "$REGION" \
            --query 'AutoScalingConfigurationSummaryList[0].AutoScalingConfigurationArn' --output text 2>/dev/null || true)
if [ -z "$ASC_ARN" ] || [ "$ASC_ARN" = "None" ]; then  # one instance: sessions live in the process's memory
  ASC_ARN=$(aws apprunner create-auto-scaling-configuration --auto-scaling-configuration-name "$APP-single" \
              --min-size 1 --max-size 1 --max-concurrency 50 --region "$REGION" --tags $TAGS \
              --query 'AutoScalingConfiguration.AutoScalingConfigurationArn' --output text)
fi

SOURCE=$(cat <<JSON
{"ImageRepository":{"ImageIdentifier":"$ECR/$APP:main","ImageRepositoryType":"ECR",
  "ImageConfiguration":{"Port":"8000",
    "RuntimeEnvironmentVariables":{"PORT":"8000","LLM_BACKEND":"anthropic","LOG_LEVEL":"INFO","REQUIRE_ACCESS_TOKEN":"true","CONSENT_SCENARIO":"default"},
    "RuntimeEnvironmentSecrets":{"ANTHROPIC_API_KEY":"$KEY_ARN","DEMO_ACCESS_TOKEN":"$TOKEN_ARN"}}},
 "AutoDeploymentsEnabled":false,
 "AuthenticationConfiguration":{"AccessRoleArn":"arn:aws:iam::$ACCOUNT:role/$ACCESS_ROLE"}}
JSON
)
HEALTH='{"Protocol":"HTTP","Path":"/healthz","Interval":10,"Timeout":5,"HealthyThreshold":1,"UnhealthyThreshold":5}'
INSTANCE="{\"Cpu\":\"0.25 vCPU\",\"Memory\":\"0.5 GB\",\"InstanceRoleArn\":\"arn:aws:iam::$ACCOUNT:role/$INSTANCE_ROLE\"}"
SERVICE_ARN=$(aws apprunner list-services --region "$REGION" --query "ServiceSummaryList[?ServiceName=='$APP'].ServiceArn | [0]" --output text)
if [ -z "$SERVICE_ARN" ] || [ "$SERVICE_ARN" = "None" ]; then
  SERVICE_ARN=$(aws apprunner create-service --service-name "$APP" --region "$REGION" --tags $TAGS \
    --source-configuration "$SOURCE" --instance-configuration "$INSTANCE" --health-check-configuration "$HEALTH" \
    --auto-scaling-configuration-arn "$ASC_ARN" --query 'Service.ServiceArn' --output text)
else  # a new image or setting: update in place (App Runner rolls the instance)
  aws apprunner update-service --service-arn "$SERVICE_ARN" --region "$REGION" --source-configuration "$SOURCE" \
    --instance-configuration "$INSTANCE" --health-check-configuration "$HEALTH" >/dev/null
fi
for _ in $(seq 1 60); do
  STATUS=$(aws apprunner describe-service --service-arn "$SERVICE_ARN" --region "$REGION" --query 'Service.Status' --output text)
  [ "$STATUS" = "RUNNING" ] && break
  case "$STATUS" in CREATE_FAILED|UPDATE_FAILED) echo "service $STATUS"; exit 1;; esac
  sleep 15
done
URL=$(aws apprunner describe-service --service-arn "$SERVICE_ARN" --region "$REGION" --query 'Service.ServiceUrl' --output text)
echo "service $STATUS: https://$URL  (access token in ~/.sop-demo-token; service arn $SERVICE_ARN)"
curl -s "https://$URL/healthz"; echo
