"""Rule thresholds. Change them here; policy.py and the docs read from this file."""
from decimal import Decimal

AMOUNT_TOLERANCE = Decimal("0.02")        # Rule 3: invoice vs purchase order
CONFIRM_THRESHOLD = Decimal("5000.00")    # Rule 4: single payment above this needs a human
DAILY_VENDOR_LIMIT = Decimal("10000.00")  # Rule 7: per vendor per day without a human

READ_TOOLS = ("read_invoice", "get_purchase_order", "lookup_vendor")
WRITE_TOOLS = ("schedule_payment",)
ALLOWED_TOOLS = READ_TOOLS + WRITE_TOOLS

# Names a fooled model might try. None of them exist; they are denied under Rule 6.
BANK_CHANGE_TOOLS = (
    "update_vendor", "update_vendor_bank_details", "change_bank_account",
    "set_vendor_bank_account", "add_vendor", "create_vendor",
)
