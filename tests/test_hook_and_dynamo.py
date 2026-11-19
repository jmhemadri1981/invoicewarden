import os

import boto3
import pytest
from moto import mock_aws

from hook.handler import lambda_handler
from invoicewarden.store import DynamoStore, MemoryStore, PaymentConflict, reference_items
from invoicewarden.tools import execute

GOOD = {"invoice_id": "INV-3001", "vendor_id": "V-1002", "po_id": "PO-2004", "amount": 1250}


def event(tool, args, session="hook-session"):
    return {"event": "before_tool_call", "name": "invoicewarden_policy_gate", "sessionId": session,
            "context": {"toolName": tool, "toolType": "inline_function", "toolInput": args,
                        "toolUseId": "tooluse_1"}}


def test_hook_allows_reads_and_marks_taint():
    store = MemoryStore()
    assert lambda_handler(event("read_invoice", {"invoice_id": "INV-3002"}), None, store=store)["decision"] == "allow"
    assert store.is_tainted("hook-session")


def test_hook_denies_bank_change_with_reason():
    out = lambda_handler(event("update_vendor_bank_details", {"vendor_id": "V-1002"}), None, store=MemoryStore())
    assert out["decision"] == "deny" and out["reason"].startswith("R6")


def test_hook_accepts_json_string_input():
    out = lambda_handler(event("lookup_vendor", '{"vendor_id": "V-1001"}'), None, store=MemoryStore())
    assert out["decision"] == "allow"


def test_hook_denies_truncated_input():
    out = lambda_handler(event("schedule_payment", {"truncated": True, "preview": "{"}), None, store=MemoryStore())
    assert out["decision"] == "deny"


def test_hook_passes_confirm_through_to_executor():
    store = MemoryStore()
    store.set_tainted("hook-session", "INV-3001")
    out = lambda_handler(event("schedule_payment", GOOD), None, store=store)
    assert out == {"decision": "allow", "reason": "CONFIRM"}


def test_hook_ignores_other_lifecycle_events():
    assert lambda_handler({"event": "after_invocation", "context": {}}, None, store=MemoryStore()) == {"decision": "allow"}


def test_hook_fails_closed_on_internal_error():
    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("database unavailable")
    out = lambda_handler(event("schedule_payment", GOOD), None, store=Broken())
    assert out["decision"] == "deny"


@pytest.fixture
def dynamo():
    with mock_aws():
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        for name in ("ref", "ledger"):
            ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST",
                             KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                                        {"AttributeName": "sk", "KeyType": "RANGE"}],
                             AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                                   {"AttributeName": "sk", "AttributeType": "S"}])
        with ddb.Table("ref").batch_writer() as batch:
            for item in reference_items():
                batch.put_item(Item=item)
        yield DynamoStore("ref", "ledger", ddb)


def test_dynamo_full_flow(dynamo):
    session = "dynamo-session"
    assert lambda_handler(event("read_invoice", {"invoice_id": "INV-3001"}, session), None, store=dynamo)["decision"] == "allow"
    assert dynamo.is_tainted(session)
    assert execute(dynamo, session, "schedule_payment", GOOD, lambda *_: False)[0] == "error"
    assert not dynamo.po_paid("PO-2004")
    status, text = execute(dynamo, session, "schedule_payment", GOOD, lambda *_: True)
    assert status == "success" and "****8765" in text
    assert dynamo.po_paid("PO-2004")
    assert execute(dynamo, session, "schedule_payment", GOOD, lambda *_: True)[0] == "error"
    outcomes = [r["outcome"] for r in dynamo.list_receipts(session)]
    assert outcomes.count("CONFIRM") == 2 and outcomes.count("DENY") == 1


def test_dynamo_daily_total_and_double_payment_guard(dynamo):
    from datetime import datetime, timezone
    from decimal import Decimal
    today = datetime.now(timezone.utc).date().isoformat()
    payment = {"payment_id": "PAY-1", "vendor_id": "V-1001", "po_id": "PO-2001", "invoice_id": "INV-3006",
               "amount": Decimal("4800.00"), "day": today, "session_id": "s", "paid_to": "****4321"}
    dynamo.record_payment(payment)
    assert dynamo.vendor_paid_on("V-1001", today) == Decimal("4800.00")
    with pytest.raises(PaymentConflict):
        dynamo.record_payment({**payment, "payment_id": "PAY-2"})
    assert dynamo.vendor_paid_on("V-1001", today) == Decimal("4800.00")
