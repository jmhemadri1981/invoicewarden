#!/usr/bin/env python3
"""Talk to the deployed InvoiceWarden agent from a terminal.

When the policy gate says a payment needs confirmation, this script asks YOU.
The model cannot answer that question; it never sees the prompt.

Usage: python scripts/chat.py            (after ./deploy/deploy.sh)
"""
import json
import sys
import uuid
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from invoicewarden.agent_client import run_turn  # noqa: E402
from invoicewarden.store import DynamoStore  # noqa: E402


def confirm(decision, tool, args):
    print("\n  +-- HUMAN CONFIRMATION REQUIRED " + "-" * 34)
    print(f"  | {tool}: {json.dumps(args)}")
    print(f"  | {', '.join(decision.rules)}: {decision.reason}")
    answer = input("  +-- Approve this payment? Type 'yes' to approve: ").strip().lower()
    return answer == "yes"


def show(record):
    print(f"  [tool] {record['tool']}({json.dumps(record['input'])}) -> {record['status']}")


def main():
    state = ROOT / ".harness.json"
    if not state.exists():
        sys.exit("No .harness.json found. Run ./deploy/deploy.sh first.")
    cfg = json.loads(state.read_text())
    client = boto3.client("bedrock-agentcore", region_name=cfg["region"], config=Config(read_timeout=300))
    store = DynamoStore(cfg["reference_table"], cfg["ledger_table"],
                        boto3.resource("dynamodb", region_name=cfg["region"]))
    session = f"{uuid.uuid4()}-iw"   # runtimeSessionId must be at least 33 characters
    print(f"InvoiceWarden. Session {session}\nTry: Please process invoice INV-3001.   (type 'receipts' or 'quit')\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text in ("quit", "exit"):
            break
        if text == "receipts":
            for r in sorted(store.list_receipts(session), key=lambda r: r["sk"]):
                print(f"  {r['at']} {r['stage']:<8} {r['tool']:<20} {r['outcome']:<7} {','.join(r['rules'])}: {r['reason']}")
            continue
        if not text:
            continue
        t = run_turn(client, cfg["harness_arn"], session, text, store, confirm, on_event=show)
        for hook in t["hook_events"]:
            if hook.get("decision") == "deny":
                print(f"  [hook] DENIED: {hook.get('reason')}")
        print(f"\nagent> {t['text'].strip()}\n")
        if t["error"]:
            print(f"  [error] {t['error']}\n")


if __name__ == "__main__":
    main()
