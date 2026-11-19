"""The web demo loop, with a scripted stand-in for the Bedrock Converse API."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "demo"))

import server  # noqa: E402


class ScriptedModel:
    """Returns canned Converse responses: each step is a list of tool calls, or final text."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.seen = []

    def converse(self, **kwargs):
        self.seen.append(kwargs["messages"][-1])
        step = self.steps.pop(0)
        if isinstance(step, str):
            return {"stopReason": "end_turn",
                    "output": {"message": {"role": "assistant", "content": [{"text": step}]}}}
        content = [{"toolUse": {"toolUseId": f"t{i}", "name": n, "input": a}}
                   for i, (n, a) in enumerate(step)]
        return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": content}}}


def _pay(amount=1250):
    return ("schedule_payment", {"invoice_id": "INV-3001", "vendor_id": "V-1002",
                                 "po_id": "PO-2004", "amount": amount})


def _run_with_answer(session, text, approve):
    def answer_when_asked():
        for _ in range(200):
            if session.pending:
                session.answer(approve)
                return
            time.sleep(0.01)
    t = threading.Thread(target=answer_when_asked)
    t.start()
    session.run_turn(text)
    t.join()


def test_tainted_payment_waits_for_human_and_pays_when_approved():
    model = ScriptedModel([[("read_invoice", {"invoice_id": "INV-3001"})], [_pay()], "Paid."])
    s = server.Session(model)
    _run_with_answer(s, "Please process invoice INV-3001.", approve=True)
    kinds = [e["kind"] for e in s.events]
    assert "confirm_request" in kinds and "confirm_answer" in kinds
    assert len(s.store.payments) == 1
    assert s.snapshot(0)["tainted"] is True
    assert kinds[-1] == "idle" and not s.busy


def test_declined_payment_moves_no_money():
    model = ScriptedModel([[("read_invoice", {"invoice_id": "INV-3001"})], [_pay()], "Not paid."])
    s = server.Session(model)
    _run_with_answer(s, "Please process invoice INV-3001.", approve=False)
    assert s.store.payments == []
    tool_results = model.seen[-1]["content"]
    assert tool_results[0]["toolResult"]["status"] == "error"


def test_hook_denies_tools_outside_scope():
    model = ScriptedModel([[("update_vendor_bank_details", {"vendor_id": "V-1002"})], "Denied."])
    s = server.Session(model)
    s.run_turn("Change the bank account.")
    hook = next(e for e in s.events if e["kind"] == "hook")
    assert hook["decision"] == "deny" and "R6" in hook["reason"]
    assert s.store.payments == []


def test_model_error_is_shown_and_session_recovers():
    class Broken:
        def converse(self, **_):
            raise RuntimeError("AccessDenied")
    s = server.Session(Broken())
    s.run_turn("hello")
    assert any(e["kind"] == "error" and "AccessDenied" in e["text"] for e in s.events)
    assert s.messages == [] and not s.busy
