"""The policy gate. Pure Python, no AWS calls, no model calls.

Both enforcement points call `gate()`:
  * the before_tool_call Lambda hook (server side, can veto any tool call)
  * the inline-function executor (client side, runs the human confirmation)

The model never decides whether a rule applies. It only proposes a tool call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import config

ALLOW, CONFIRM, DENY = "ALLOW", "CONFIRM", "DENY"

_VENDOR_ID = re.compile(r"^V-\d{4}$")
_PO_ID = re.compile(r"^PO-\d{4}$")
_INVOICE_ID = re.compile(r"^INV-\d{4}$")


@dataclass
class Decision:
    outcome: str
    rules: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self):
        return asdict(self)


def _deny(rule, reason):
    return Decision(DENY, [rule], reason)


def normalize_tool_name(name: str) -> str:
    """Strip any server prefix a harness may add (`server___tool`, `@server/tool`)."""
    name = (name or "").strip()
    for sep in ("___", "/"):
        if sep in name:
            name = name.rsplit(sep, 1)[-1]
    return name


def _money(value):
    if isinstance(value, bool) or value is None:
        raise InvalidOperation
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise InvalidOperation
    return amount.quantize(Decimal("0.01"))


def evaluate(store, session_id, tool, args, *, confirmed=False, today=None) -> Decision:
    """Return ALLOW, CONFIRM (needs a human) or DENY for one proposed tool call."""
    tool = normalize_tool_name(tool)
    today = today or datetime.now(timezone.utc).date().isoformat()

    if tool in config.BANK_CHANGE_TOOLS:
        return _deny("R6", "Vendor master and bank details cannot be changed by the agent.")
    if tool not in config.ALLOWED_TOOLS:
        return _deny("R1", f"Tool '{tool}' is outside the agent's scope.")
    if not isinstance(args, dict) or args.get("truncated") is True:
        return _deny("R9", "Tool input is missing or incomplete. Stop and ask a human.")

    if tool == "read_invoice":
        if not _INVOICE_ID.match(str(args.get("invoice_id", ""))):
            return _deny("R9", "invoice_id must look like INV-0000.")
        return Decision(ALLOW, ["R1"], "Read access to invoices is in scope.")

    if tool == "get_purchase_order":
        if not _PO_ID.match(str(args.get("po_id", ""))):
            return _deny("R9", "po_id must look like PO-0000.")
        return Decision(ALLOW, ["R1"], "Read access to purchase orders is in scope.")

    if tool == "lookup_vendor":
        if not _VENDOR_ID.match(str(args.get("vendor_id", ""))):
            return _deny("R8", "Vendor lookups are one exact vendor ID at a time. No lists, no wildcards.")
        return Decision(ALLOW, ["R1", "R8"], "Single-vendor lookup with masked bank details.")

    # ---- schedule_payment ------------------------------------------------
    vendor_id = str(args.get("vendor_id", ""))
    po_id = str(args.get("po_id", ""))
    invoice_id = str(args.get("invoice_id", ""))
    try:
        amount = _money(args.get("amount"))
    except (InvalidOperation, ValueError, TypeError):
        return _deny("R9", "amount is missing or not a number. Stop and ask a human.")
    if amount <= 0:
        return _deny("R9", "amount must be positive. Stop and ask a human.")
    extra = set(args) - {"vendor_id", "po_id", "invoice_id", "amount"}
    if extra:
        # e.g. a smuggled bank_account argument
        return _deny("R6", f"Unexpected payment fields {sorted(extra)}. Payments go only to the account on file.")

    vendor = store.get_vendor(vendor_id) if _VENDOR_ID.match(vendor_id) else None
    if not vendor or vendor.get("status") != "active":
        return _deny("R2", f"Vendor '{vendor_id}' is not an active vendor in the vendor master.")

    invoice = store.get_invoice(invoice_id) if _INVOICE_ID.match(invoice_id) else None
    if not invoice:
        return _deny("R9", f"Invoice '{invoice_id}' is not on file. Stop and ask a human.")

    # The payment must be the one this invoice describes. Without this check a fooled
    # model can attach an unrelated invoice to another vendor's open purchase order
    # (found by scripts/stress_test.py, attack 6; see docs/FINDINGS.md).
    if invoice.get("vendor_id") != vendor_id:
        return _deny("R2", f"Invoice {invoice_id} was not issued by vendor {vendor_id}.")
    if invoice.get("po_id") != po_id:
        return _deny("R3", f"Invoice {invoice_id} does not reference purchase order {po_id}.")
    try:
        invoice_amount = _money(invoice.get("amount"))
    except (InvalidOperation, ValueError, TypeError):
        return _deny("R9", f"Invoice {invoice_id} has no readable amount. Stop and ask a human.")
    if amount != invoice_amount:
        return _deny("R3", f"Amount {amount} is not the invoiced amount {invoice_amount}.")

    po = store.get_po(po_id) if _PO_ID.match(po_id) else None
    if not po:
        return _deny("R3", f"Purchase order '{po_id}' does not exist.")
    if po["vendor_id"] != vendor_id:
        return _deny("R3", f"Purchase order {po_id} belongs to a different vendor.")
    if store.po_paid(po_id):
        return _deny("R3", f"Purchase order {po_id} has already been paid.")
    po_amount = _money(po["amount"])
    if abs(amount - po_amount) > po_amount * config.AMOUNT_TOLERANCE:
        return _deny("R3", f"Amount {amount} differs from purchase order amount {po_amount} "
                           f"by more than {config.AMOUNT_TOLERANCE:.0%}.")

    rules, reasons = [], []
    if amount > config.CONFIRM_THRESHOLD:
        rules.append("R4")
        reasons.append(f"amount {amount} is above {config.CONFIRM_THRESHOLD}")
    if store.is_tainted(session_id):
        rules.append("R5")
        reasons.append("this session has read external document content")
    paid_today = store.vendor_paid_on(vendor_id, today)
    if paid_today + amount > config.DAILY_VENDOR_LIMIT:
        rules.append("R7")
        reasons.append(f"vendor daily total would reach {paid_today + amount} "
                       f"(limit {config.DAILY_VENDOR_LIMIT})")

    if rules and not confirmed:
        return Decision(CONFIRM, rules, "Human confirmation required: " + "; ".join(reasons) + ".")
    if rules:
        return Decision(ALLOW, rules, "Approved by a human: " + "; ".join(reasons) + ".")
    return Decision(ALLOW, ["R2", "R3"], "Vendor, purchase order and amount all match.")


def gate(store, session_id, stage, tool, args, *, confirmed=False, today=None) -> Decision:
    """evaluate() plus its two side effects: the taint flag and the decision receipt."""
    tool = normalize_tool_name(tool)
    decision = evaluate(store, session_id, tool, args, confirmed=confirmed, today=today)
    if tool == "read_invoice" and decision.outcome == ALLOW:
        store.set_tainted(session_id, source=str(args.get("invoice_id")))
    store.add_receipt({
        "session_id": session_id,
        "stage": stage,
        "tool": tool,
        "input": args if isinstance(args, dict) else {"raw": str(args)[:500]},
        "outcome": decision.outcome,
        "rules": decision.rules,
        "reason": decision.reason,
        "confirmed": confirmed,
        "tainted": store.is_tainted(session_id),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return decision
