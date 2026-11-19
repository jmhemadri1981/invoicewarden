#!/usr/bin/env bash
# Deploys InvoiceWarden: CloudFormation stack, hook code, seed data, harness.
# Usage: AWS_REGION=us-east-1 ./deploy/deploy.sh [extra args for create_harness.py]
set -euo pipefail
cd "$(dirname "$0")/.."

STACK="${STACK_NAME:-invoicewarden}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

echo "==> 1/4 CloudFormation stack '$STACK' in $REGION"
aws cloudformation deploy --stack-name "$STACK" --template-file deploy/template.yaml \
  --capabilities CAPABILITY_NAMED_IAM --no-fail-on-empty-changeset

echo "==> 2/4 Hook Lambda code"
rm -rf build && mkdir -p build
zip -qr build/hook.zip hook invoicewarden -x '*/__pycache__/*' '*.pyc'
aws lambda update-function-code --function-name "$STACK-policy-gate" \
  --zip-file fileb://build/hook.zip --query LastModified --output text
aws lambda wait function-updated --function-name "$STACK-policy-gate"

echo "==> 3/4 Seed data"
python3 deploy/seed_data.py --stack "$STACK"

echo "==> 4/4 AgentCore harness"
python3 deploy/create_harness.py --stack "$STACK" "$@"

echo
echo "Done. Try:  python3 scripts/chat.py"
