"""Storage. MemoryStore for tests and the offline stress test, DynamoStore for AWS.

Two tables, on purpose:
  * reference: vendors, purchase orders, invoices. Nothing at runtime can write to it.
  * ledger:    session taint flags, payments, decision receipts.
"""
from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed.json"


class PaymentConflict(Exception):
    """The purchase order was paid by someone else between the check and the write."""


def load_seed(path=SEED_PATH):
    return json.loads(Path(path).read_text(), parse_float=Decimal)


class MemoryStore:
    def __init__(self, seed=None):
        seed = seed if seed is not None else load_seed()
        self.vendors = {v["vendor_id"]: v for v in seed["vendors"]}
        self.pos = {p["po_id"]: p for p in seed["purchase_orders"]}
        self.invoices = {i["invoice_id"]: i for i in seed["invoices"]}
        self.tainted, self.payments, self.receipts = {}, [], []

    def get_vendor(self, vendor_id): return self.vendors.get(vendor_id)
    def get_po(self, po_id): return self.pos.get(po_id)
    def get_invoice(self, invoice_id): return self.invoices.get(invoice_id)

    def is_tainted(self, session_id): return session_id in self.tainted
    def set_tainted(self, session_id, source): self.tainted.setdefault(session_id, source)

    def po_paid(self, po_id): return any(p["po_id"] == po_id for p in self.payments)

    def vendor_paid_on(self, vendor_id, day):
        return sum((p["amount"] for p in self.payments
                    if p["vendor_id"] == vendor_id and p["day"] == day), Decimal("0"))

    def record_payment(self, payment):
        if self.po_paid(payment["po_id"]):
            raise PaymentConflict(payment["po_id"])
        self.payments.append(dict(payment))

    def add_receipt(self, receipt): self.receipts.append(dict(receipt))
    def list_receipts(self, session_id):
        return [r for r in self.receipts if r["session_id"] == session_id]


class DynamoStore:
    def __init__(self, reference_table=None, ledger_table=None, resource=None):
        import boto3
        self._ddb = resource or boto3.resource("dynamodb")
        self._ref_name = reference_table or os.environ["REFERENCE_TABLE"]
        self._led_name = ledger_table or os.environ["LEDGER_TABLE"]
        self._ref = self._ddb.Table(self._ref_name)
        self._led = self._ddb.Table(self._led_name)

    def _ref_get(self, pk):
        return self._ref.get_item(Key={"pk": pk, "sk": "META"}).get("Item")

    def get_vendor(self, vendor_id): return self._ref_get(f"VENDOR#{vendor_id}")
    def get_po(self, po_id): return self._ref_get(f"PO#{po_id}")
    def get_invoice(self, invoice_id): return self._ref_get(f"INVOICE#{invoice_id}")

    def is_tainted(self, session_id):
        key = {"pk": f"SESSION#{session_id}", "sk": "TAINT"}
        return "Item" in self._led.get_item(Key=key, ConsistentRead=True)

    def set_tainted(self, session_id, source):
        self._led.put_item(Item={"pk": f"SESSION#{session_id}", "sk": "TAINT", "source": source})

    def po_paid(self, po_id):
        key = {"pk": f"POPAID#{po_id}", "sk": "STATE"}
        return "Item" in self._led.get_item(Key=key, ConsistentRead=True)

    def vendor_paid_on(self, vendor_id, day):
        from boto3.dynamodb.conditions import Key
        total, kwargs = Decimal("0"), {
            "KeyConditionExpression": Key("pk").eq(f"PAYDAY#{vendor_id}#{day}"),
            "ConsistentRead": True,
        }
        while True:
            page = self._led.query(**kwargs)
            total += sum((i["amount"] for i in page["Items"]), Decimal("0"))
            if "LastEvaluatedKey" not in page:
                return total
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def record_payment(self, payment):
        from botocore.exceptions import ClientError
        item = dict(payment)
        try:
            self._ddb.meta.client.transact_write_items(TransactItems=[
                {"Put": {"TableName": self._led_name,
                         "Item": {"pk": f"POPAID#{item['po_id']}", "sk": "STATE",
                                  "payment_id": item["payment_id"]},
                         "ConditionExpression": "attribute_not_exists(pk)"}},
                {"Put": {"TableName": self._led_name,
                         "Item": {"pk": f"PAYDAY#{item['vendor_id']}#{item['day']}",
                                  "sk": item["payment_id"], **item}}},
            ])
        except ClientError as err:
            if err.response["Error"]["Code"] == "TransactionCanceledException":
                raise PaymentConflict(item["po_id"]) from err
            raise

    def add_receipt(self, receipt):
        item = json.loads(json.dumps(receipt, default=str), parse_float=Decimal)
        item["pk"] = f"RECEIPT#{receipt['session_id']}"
        item["sk"] = f"{receipt['at']}#{uuid.uuid4().hex[:8]}"
        self._led.put_item(Item=item)

    def list_receipts(self, session_id):
        from boto3.dynamodb.conditions import Key
        items, kwargs = [], {"KeyConditionExpression": Key("pk").eq(f"RECEIPT#{session_id}")}
        while True:
            page = self._led.query(**kwargs)
            items += page["Items"]
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def reference_items(seed=None):
    """Seed data as reference-table items (used by deploy/seed_data.py and the tests)."""
    seed = seed if seed is not None else load_seed()
    for v in seed["vendors"]:
        yield {"pk": f"VENDOR#{v['vendor_id']}", "sk": "META", **v}
    for p in seed["purchase_orders"]:
        yield {"pk": f"PO#{p['po_id']}", "sk": "META", **p}
    for i in seed["invoices"]:
        yield {"pk": f"INVOICE#{i['invoice_id']}", "sk": "META", **i}
