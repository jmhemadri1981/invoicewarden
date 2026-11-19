import importlib.util
from pathlib import Path

from invoicewarden.agent_client import run_turn
from invoicewarden.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("stress_test", ROOT / "scripts" / "stress_test.py")
stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stress)


def test_offline_stress_test_holds_every_rule():
    results = stress.run_offline()
    assert len(results) >= 6
    assert [r["id"] for r in results if not r["held"]] == []
    assert any("ignore all previous instructions" in s[3].lower() for s in results[0]["steps"])


class FakeHarness:
    """Replays InvokeHarness streams shaped like the documented event format."""
    def __init__(self, streams):
        self.streams, self.requests = list(streams), []

    def invoke_harness(self, **kwargs):
        self.requests.append(kwargs)
        return {"stream": self.streams.pop(0)}


def tool_stream(tool_use_id, name, raw, deny=False):
    events = [{"messageStart": {"role": "assistant"}},
              {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": tool_use_id, "name": name}}}},
              {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": raw[:5]}}}},
              {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": raw[5:]}}}},
              {"contentBlockStop": {"contentBlockIndex": 0}}]
    if deny:
        events += [{"hookEvent": {"type": "before_tool_call", "decision": "deny", "reason": "R6"}},
                   {"messageStop": {"stopReason": "tool_use"}},
                   {"messageStart": {"role": "assistant"}},
                   {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "I cannot do that."}}},
                   {"messageStop": {"stopReason": "end_turn"}}]
    else:
        events += [{"hookEvent": {"type": "before_tool_call", "decision": "allow"}},
                   {"messageStop": {"stopReason": "tool_use"}}]
    return events


def text_stream(text):
    return [{"messageStart": {"role": "assistant"}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}},
            {"messageStop": {"stopReason": "end_turn"}}]


def test_client_runs_inline_handoff_and_returns_tool_result():
    store = MemoryStore()
    harness = FakeHarness([tool_stream("t1", "lookup_vendor", '{"vendor_id": "V-1001"}'), text_stream("Done.")])
    t = run_turn(harness, "arn", "s" * 36, "look up V-1001", store, lambda *_: False)
    assert t["text"] == "Done." and t["stop"] == "end_turn"
    result = harness.requests[1]["messages"][0]["content"][0]["toolResult"]
    assert result["toolUseId"] == "t1" and result["status"] == "success" and "****4321" in result["content"][0]["text"]


def test_client_does_not_execute_a_call_the_hook_denied():
    store = MemoryStore()
    harness = FakeHarness([tool_stream("t1", "update_vendor_bank_details", '{"vendor_id": "V-1002"}', deny=True)])
    t = run_turn(harness, "arn", "s" * 36, "change the bank account", store, lambda *_: True)
    assert len(harness.requests) == 1 and t["text"] == "I cannot do that."
    assert store.receipts == []          # the executor never ran
