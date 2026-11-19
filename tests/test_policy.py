from decimal import Decimal

import pytest

from invoicewarden.policy import ALLOW, CONFIRM, DENY, evaluate, gate
from invoicewarden.store import MemoryStore
from invoicewarden.tools import execute

S, DAY = "session-under-test", "2026-11-20"
GOOD = {"invoice_id": "INV-3001", "vendor_id": "V-1002", "po_id": "PO-2004", "amount": 1250}


@pytest.fixture
def store():
    return MemoryStore()


def pay(store, args, **kw):
    return evaluate(store, S, "schedule_payment", args, today=DAY, **kw)


def test_clean_payment_in_untainted_session_is_allowed(store):
    assert pay(store, GOOD).outcome == ALLOW


@pytest.mark.parametrize("tool", ["shell", "file_operations", "list_vendors", "delete_payment", ""])
def test_r1_unknown_tools_are_denied(store, tool):
    d = evaluate(store, S, tool, {})
    assert (d.outcome, d.rules) == (DENY, ["R1"])


@pytest.mark.parametrize("vendor", ["V-9999", "V-1003", "", "v-1002"])
def test_r2_unknown_or_inactive_vendor_is_denied(store, vendor):
    d = pay(store, {**GOOD, "vendor_id": vendor})
    assert (d.outcome, d.rules) == (DENY, ["R2"])


def test_r3_amount_must_match_purchase_order(store):
    d = pay(store, {"invoice_id": "INV-3004", "vendor_id": "V-1001", "po_id": "PO-2007", "amount": 3240})
    assert (d.outcome, d.rules) == (DENY, ["R3"])


def test_r3_amount_must_be_the_invoiced_amount(store):
    assert pay(store, {**GOOD, "amount": 1260}).rules == ["R3"]


def test_r3_purchase_order_of_another_vendor_is_denied(store):
    d = pay(store, {**GOOD, "po_id": "PO-2001"})
    assert (d.outcome, d.rules) == (DENY, ["R3"])


def test_r3_purchase_order_cannot_be_paid_twice(store):
    assert execute(store, S, "schedule_payment", GOOD, lambda *_: True, today=DAY)[0] == "success"
    d = pay(store, GOOD)
    assert d.outcome == DENY and "already been paid" in d.reason


def test_r4_large_payment_needs_confirmation(store):
    args = {"invoice_id": "INV-3003", "vendor_id": "V-1002", "po_id": "PO-2005", "amount": 7400}
    assert (pay(store, args).outcome, pay(store, args).rules) == (CONFIRM, ["R4"])
    assert pay(store, args, confirmed=True).outcome == ALLOW


def test_r5_reading_an_invoice_taints_the_session(store):
    assert pay(store, GOOD).outcome == ALLOW
    gate(store, S, "hook", "read_invoice", {"invoice_id": "INV-3001"})
    d = pay(store, GOOD)
    assert (d.outcome, d.rules) == (CONFIRM, ["R5"])
    assert evaluate(store, "another-session", "schedule_payment", GOOD, today=DAY).outcome == ALLOW


def test_r5_denied_read_does_not_taint(store):
    gate(store, S, "hook", "read_invoice", {"invoice_id": "../../etc/passwd"})
    assert not store.is_tainted(S)


@pytest.mark.parametrize("tool", ["update_vendor_bank_details", "change_bank_account", "create_vendor"])
def test_r6_no_bank_change_tools(store, tool):
    d = evaluate(store, S, tool, {"vendor_id": "V-1002", "bank_account": "X"})
    assert (d.outcome, d.rules) == (DENY, ["R6"])


def test_r6_payment_cannot_carry_a_bank_account(store):
    d = pay(store, {**GOOD, "bank_account": "TEST-ATTACKER-9999"})
    assert (d.outcome, d.rules) == (DENY, ["R6"])


def test_r6_payment_always_goes_to_account_on_file(store):
    status, text = execute(store, S, "schedule_payment", GOOD, lambda *_: True, today=DAY)
    assert status == "success" and store.payments[0]["paid_to"] == "****8765"


def test_r7_daily_vendor_limit(store):
    calls = [("INV-3006", "PO-2001", 4800), ("INV-3007", "PO-2002", 4900), ("INV-3008", "PO-2003", 4700)]
    outcomes = []
    for inv, po, amount in calls:
        args = {"invoice_id": inv, "vendor_id": "V-1001", "po_id": po, "amount": amount}
        outcomes.append(pay(store, args).outcome)
        execute(store, S, "schedule_payment", args, lambda *_: False, today=DAY)
    assert outcomes == [ALLOW, ALLOW, CONFIRM]
    assert store.vendor_paid_on("V-1001", DAY) == Decimal("9700.00")
    assert store.vendor_paid_on("V-1001", "2026-11-21") == 0


@pytest.mark.parametrize("vendor", ["*", "V-1001,V-1002", "V-1001' OR '1'='1", "", None])
def test_r8_vendor_lookup_is_one_exact_id(store, vendor):
    d = evaluate(store, S, "lookup_vendor", {"vendor_id": vendor})
    assert (d.outcome, d.rules) == (DENY, ["R8"])


def test_r8_lookup_masks_bank_account(store):
    status, text = execute(store, S, "lookup_vendor", {"vendor_id": "V-1001"}, lambda *_: False)
    assert status == "success" and "TEST-ACCT" not in text and "****4321" in text


@pytest.mark.parametrize("args", [None, "pay everything", [], {"truncated": True, "preview": "{"}])
def test_r9_malformed_input_is_denied(store, args):
    d = evaluate(store, S, "schedule_payment", args)
    assert (d.outcome, d.rules) == (DENY, ["R9"])


@pytest.mark.parametrize("amount", [None, "lots", -5, 0, True, float("nan"), float("inf")])
def test_r9_bad_amount_is_denied(store, amount):
    d = pay(store, {**GOOD, "amount": amount})
    assert (d.outcome, d.rules) == (DENY, ["R9"])


def test_r9_invoice_not_on_file(store):
    assert pay(store, {**GOOD, "invoice_id": "INV-9999"}).rules == ["R9"]


def test_invoice_must_belong_to_the_vendor_being_paid(store):
    d = pay(store, {"invoice_id": "INV-3005", "vendor_id": "V-1002", "po_id": "PO-2004", "amount": 1250})
    assert (d.outcome, d.rules) == (DENY, ["R2"])


def test_declined_confirmation_moves_no_money(store):
    gate(store, S, "hook", "read_invoice", {"invoice_id": "INV-3001"})
    status, text = execute(store, S, "schedule_payment", GOOD, lambda *_: False, today=DAY)
    assert status == "error" and "declined" in text and store.payments == []


def test_every_decision_leaves_a_receipt(store):
    gate(store, S, "hook", "read_invoice", {"invoice_id": "INV-3001"})
    execute(store, S, "schedule_payment", GOOD, lambda *_: True, today=DAY)
    assert [(r["stage"], r["outcome"], r["confirmed"]) for r in store.list_receipts(S)] == [
        ("hook", "ALLOW", False), ("executor", "CONFIRM", False), ("executor", "ALLOW", True)]


def test_prefixed_tool_names_are_normalised(store):
    assert evaluate(store, S, "invoicewarden___lookup_vendor", {"vendor_id": "V-1001"}).outcome == ALLOW
    assert evaluate(store, S, "@x/update_vendor_bank_details", {}).rules == ["R6"]
