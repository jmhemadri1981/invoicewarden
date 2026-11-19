#!/usr/bin/env bash
# Removes the harness and the CloudFormation stack.
set -euo pipefail
cd "$(dirname "$0")/.."
STACK="${STACK_NAME:-invoicewarden}"
if [ -f .harness.json ]; then
  HARNESS_ID=$(python3 -c "import json;print(json.load(open('.harness.json'))['harness_id'])")
  aws bedrock-agentcore-control delete-harness --harness-id "$HARNESS_ID" || true
  rm -f .harness.json
fi
aws cloudformation delete-stack --stack-name "$STACK"
aws cloudformation wait stack-delete-complete --stack-name "$STACK"
echo "removed"
