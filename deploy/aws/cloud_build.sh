#!/usr/bin/env bash
# Build the container image in AWS CodeBuild (no local Docker needed) and push it to ECR.
# Run from the repo root with a signed-in AWS CLI: bash deploy/aws/cloud_build.sh
# Creates, once: an ECR repository, a private S3 bucket for the source zip, a CodeBuild role and project.
set -euo pipefail
export MSYS_NO_PATHCONV=1
REGION="${AWS_REGION:-us-east-1}"; APP="sop-claims-agent"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="$APP-build-$ACCOUNT"
ECR="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
TAGS="Key=Project,Value=$APP"

ZIP=$(mktemp -d)/source.zip
python - "$ZIP" <<'EOF'
import pathlib, sys, zipfile
out = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.write("deploy/aws/buildspec.yml", "buildspec.yml")
    for top in ("Dockerfile", "pyproject.toml"):
        z.write(top, top)
    for d in ("app", "ui", "fixtures"):
        for p in sorted(pathlib.Path(d).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.as_posix())
EOF

aws ecr describe-repositories --repository-names "$APP" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$APP" --region "$REGION" --image-scanning-configuration scanOnPush=true --tags $TAGS >/dev/null
aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3 cp "$ZIP" "s3://$BUCKET/source.zip" --only-show-errors

ROLE="$APP-codebuild"
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"codebuild.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam get-role --role-name "$ROLE" >/dev/null 2>&1 || aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" --tags $TAGS >/dev/null
POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"arn:aws:logs:$REGION:$ACCOUNT:log-group:/aws/codebuild/$APP*"},
 {"Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion"],"Resource":"arn:aws:s3:::$BUCKET/*"},
 {"Effect":"Allow","Action":["ecr:GetAuthorizationToken"],"Resource":"*"},
 {"Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:CompleteLayerUpload","ecr:InitiateLayerUpload","ecr:PutImage","ecr:UploadLayerPart","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer"],"Resource":"arn:aws:ecr:$REGION:$ACCOUNT:repository/$APP"}]}
JSON
)
aws iam put-role-policy --role-name "$ROLE" --policy-name build-and-push-$APP --policy-document "$POLICY"
sleep 10  # IAM propagation

ENV_JSON="{\"type\":\"LINUX_CONTAINER\",\"image\":\"aws/codebuild/amazonlinux2-x86_64-standard:5.0\",\"computeType\":\"BUILD_GENERAL1_SMALL\",\"privilegedMode\":true,\"environmentVariables\":[{\"name\":\"ECR\",\"value\":\"$ECR\"},{\"name\":\"APP\",\"value\":\"$APP\"},{\"name\":\"AWS_REGION\",\"value\":\"$REGION\"}]}"
SRC_JSON="{\"type\":\"S3\",\"location\":\"$BUCKET/source.zip\"}"
if aws codebuild batch-get-projects --names "$APP" --region "$REGION" --query 'projects[0].name' --output text 2>/dev/null | grep -q "^$APP$"; then
  aws codebuild update-project --name "$APP" --region "$REGION" --source "$SRC_JSON" --environment "$ENV_JSON" \
    --service-role "arn:aws:iam::$ACCOUNT:role/$ROLE" --artifacts type=NO_ARTIFACTS >/dev/null
else
  aws codebuild create-project --name "$APP" --region "$REGION" --source "$SRC_JSON" --environment "$ENV_JSON" \
    --service-role "arn:aws:iam::$ACCOUNT:role/$ROLE" --artifacts type=NO_ARTIFACTS --tags key=Project,value=$APP >/dev/null
fi
BUILD_ID=$(aws codebuild start-build --project-name "$APP" --region "$REGION" --query 'build.id' --output text)
echo "build started: $BUILD_ID"
for _ in $(seq 1 60); do
  STATUS=$(aws codebuild batch-get-builds --ids "$BUILD_ID" --region "$REGION" --query 'builds[0].buildStatus' --output text)
  case "$STATUS" in SUCCEEDED) break;; FAILED|FAULT|STOPPED|TIMED_OUT) echo "build $STATUS"; exit 1;; esac
  sleep 15
done
echo "build $STATUS: $ECR/$APP:main"
