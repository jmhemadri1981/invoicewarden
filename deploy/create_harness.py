#!/usr/bin/env python3
"""Create (or update) the InvoiceWarden harness on Amazon Bedrock AgentCore.

Writes .harness.json, which scripts/chat.py and scripts/stress_test.py --live read.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stack import stack_outputs  # noqa: E402
from invoicewarden.prompts import SYSTEM_PROMPT  # noqa: E402
from invoicewarden.tools import TOOL_SPECS  # noqa: E402

HARNESS_NAME = "InvoiceWarden"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="invoicewarden")
    parser.add_argument("--region")
    parser.add_argument("--model-id", help="Bedrock model ID. Default: the AgentCore service default.")
    parser.add_argument("--guardrail-arn", help="Optional Bedrock Guardrail ARN (needs --model-id).")
    parser.add_argument("--guardrail-version", default="DRAFT")
    parser.add_argument("--allowed-tools", nargs="*",
                        help="Optional allowedTools selectors. Leave unset unless you have confirmed the "
                             "selector syntax for inline functions; the hook denies other tools either way.")
    args = parser.parse_args()
    if args.guardrail_arn and not args.model_id:
        parser.error("--guardrail-arn needs --model-id")

    region = args.region or boto3.session.Session().region_name
    out = stack_outputs(args.stack, region)
    control = boto3.client("bedrock-agentcore-control", region_name=region)

    config = {
        "executionRoleArn": out["HarnessRoleArn"],
        "systemPrompt": [{"text": SYSTEM_PROMPT}],
        "tools": TOOL_SPECS,
        "hooks": [{"beforeToolCall": {
            "name": "invoicewarden_policy_gate",
            "target": {"lambda": {"arn": out["HookFunctionArn"], "timeoutSeconds": 10,
                                  "failureMode": "deny"}},   # fail closed
        }}],
        "maxIterations": 12,
        "timeoutSeconds": 300,
    }
    if args.allowed_tools:
        config["allowedTools"] = args.allowed_tools
    if args.model_id:
        model = {"modelId": args.model_id}
        if args.guardrail_arn:
            model["additionalParams"] = {"guardrailConfig": {
                "guardrailIdentifier": args.guardrail_arn,
                "guardrailVersion": args.guardrail_version, "trace": "enabled"}}
        config["model"] = {"bedrockModelConfig": model}

    existing = None
    state_file = ROOT / ".harness.json"
    if state_file.exists():
        existing = json.loads(state_file.read_text()).get("harness_id")
    if existing:
        print(f"updating harness {existing}")
        harness = control.update_harness(harnessId=existing, **config)["harness"]
    else:
        print(f"creating harness {HARNESS_NAME}")
        harness = control.create_harness(harnessName=HARNESS_NAME, **config)["harness"]

    harness_id = harness["harnessId"]
    for _ in range(90):
        harness = control.get_harness(harnessId=harness_id)["harness"]
        status = harness["status"]
        print("  status:", status)
        if status == "READY":
            break
        if "FAIL" in status:
            sys.exit(f"harness failed: {harness.get('failureReason')}")
        time.sleep(5)
    else:
        sys.exit("timed out waiting for the harness to become READY")

    state_file.write_text(json.dumps({
        "harness_id": harness_id, "harness_arn": harness["arn"], "region": region,
        "reference_table": out["ReferenceTable"], "ledger_table": out["LedgerTable"],
    }, indent=2) + "\n")
    print(f"harness ready: {harness['arn']}\nwrote {state_file.name}")


if __name__ == "__main__":
    main()
