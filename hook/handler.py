"""before_tool_call lifecycle hook for the AgentCore harness.

The harness calls this Lambda synchronously before every tool call, including
inline functions. A `deny` skips the tool call. The hook is configured with
failureMode=deny, and it also denies on any internal error, so it fails closed.
"""
import json
import logging

from invoicewarden.policy import DENY, gate
from invoicewarden.store import DynamoStore

log = logging.getLogger()
log.setLevel(logging.INFO)
_store = None


def _get_store():
    global _store
    if _store is None:
        _store = DynamoStore()
    return _store


def lambda_handler(event, _context, store=None):
    try:
        if event.get("event") != "before_tool_call":
            return {"decision": "allow"}
        ctx = event.get("context") or {}
        args = ctx.get("toolInput")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = None
        decision = gate(store or _get_store(), event.get("sessionId", "unknown"),
                        "hook", ctx.get("toolName", ""), args)
        log.info(json.dumps({"receipt": "hook", "session": event.get("sessionId"),
                             "tool": ctx.get("toolName"), **decision.to_dict()}))
        if decision.outcome == DENY:
            reason = f"{', '.join(decision.rules)}: {decision.reason}"
            return {"decision": "deny", "reason": reason[:1000]}
        # ALLOW and CONFIRM both proceed to the executor, which runs the human step.
        return {"decision": "allow", "reason": decision.outcome}
    except Exception:  # noqa: BLE001 - fail closed on anything unexpected
        log.exception("hook error")
        return {"decision": "deny", "reason": "R9: policy check failed. Stop and ask a human."}
