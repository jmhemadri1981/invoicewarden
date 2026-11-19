#!/usr/bin/env bash
# Start the local web demo on http://localhost:8000
#   AWS_PROFILE=<profile with bedrock:InvokeModel> ./demo/run.sh
# Optional: IW_MODEL_ID (default us.anthropic.claude-sonnet-4-6), AWS_REGION (default us-east-1), IW_PORT
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
export AWS_REGION="${AWS_REGION:-us-east-1}"
export IW_MODEL_ID="${IW_MODEL_ID:-us.anthropic.claude-sonnet-4-6}"
exec .venv/bin/python demo/server.py
