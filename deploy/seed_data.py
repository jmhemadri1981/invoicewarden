#!/usr/bin/env python3
"""Load data/seed.json into the reference table. Run by an admin, never by the agent.

--reset-ledger also clears payments, taint flags and receipts, so the live stress
test can be repeated from a clean state.
"""
import argparse
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stack import stack_outputs  # noqa: E402
from invoicewarden.store import reference_items  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stack", default="invoicewarden")
    parser.add_argument("--region")
    parser.add_argument("--reset-ledger", action="store_true")
    args = parser.parse_args()

    out = stack_outputs(args.stack, args.region)
    ddb = boto3.resource("dynamodb", region_name=args.region)
    count = 0
    with ddb.Table(out["ReferenceTable"]).batch_writer() as batch:
        for item in reference_items():
            batch.put_item(Item=item)
            count += 1
    print(f"seeded {count} reference items into {out['ReferenceTable']}")

    if args.reset_ledger:
        ledger, removed, kwargs = ddb.Table(out["LedgerTable"]), 0, {"ProjectionExpression": "pk, sk"}
        while True:
            page = ledger.scan(**kwargs)
            with ledger.batch_writer() as batch:
                for item in page["Items"]:
                    batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
                    removed += 1
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        print(f"cleared {removed} ledger items from {out['LedgerTable']}")


if __name__ == "__main__":
    main()
