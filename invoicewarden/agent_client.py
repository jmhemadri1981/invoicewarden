"""Client loop for the AgentCore harness: send a message, run inline tool handoffs.

Stream handling follows the InvokeHarness docs (contentBlockStart / contentBlockDelta /
messageStop / hookEvent). A tool call is only executed when the stream ends with
stopReason == "tool_use", which is the harness saying it is waiting for our result.
"""
from __future__ import annotations

import json

from .tools import TOOL_SPECS, execute


def _consume(stream):
    text, calls, hooks, stop, error = [], [], [], None, None
    by_index, current = {}, None
    for event in stream:
        if "messageStart" in event:
            by_index, current = {}, None
        elif "contentBlockStart" in event:
            block = event["contentBlockStart"]
            start = block.get("start", {})
            if "toolUse" in start:
                current = {"id": start["toolUse"]["toolUseId"],
                           "name": start["toolUse"].get("name", ""),
                           "raw": "", "denied": False}
                by_index[block.get("contentBlockIndex")] = current
                calls.append(current)
            elif "toolResult" in start:
                # The harness produced a result itself (for example a hook denial).
                done = start["toolResult"].get("toolUseId")
                for call in calls:
                    if call["id"] == done:
                        call["denied"] = True
        elif "contentBlockDelta" in event:
            block = event["contentBlockDelta"]
            delta = block.get("delta", {})
            if "text" in delta:
                text.append(delta["text"])
            elif "toolUse" in delta:
                call = by_index.get(block.get("contentBlockIndex"))
                if call:
                    call["raw"] += delta["toolUse"].get("input", "")
        elif "hookEvent" in event:
            hook = event["hookEvent"]
            hooks.append(hook)
            if hook.get("type") == "before_tool_call" and hook.get("decision") == "deny":
                pending = [c for c in calls if not c["denied"]]
                if pending:
                    pending[0]["denied"] = True
        elif "messageStop" in event:
            stop = event["messageStop"].get("stopReason")
        elif "runtimeClientError" in event:
            error = event["runtimeClientError"].get("message")
    return {"text": "".join(text), "calls": calls, "hooks": hooks, "stop": stop, "error": error}


def run_turn(client, harness_arn, session_id, user_text, store, confirm_fn,
             max_handoffs=12, on_event=None):
    """Send one user message and resolve every tool handoff. Returns a transcript dict."""
    messages = [{"role": "user", "content": [{"text": user_text}]}]
    transcript = {"prompt": user_text, "text": "", "tool_calls": [], "hook_events": [],
                  "stop": None, "error": None}
    for _ in range(max_handoffs + 1):
        response = client.invoke_harness(harnessArn=harness_arn, runtimeSessionId=session_id,
                                         tools=TOOL_SPECS, messages=messages)
        part = _consume(response["stream"])
        transcript["text"] += part["text"]
        transcript["hook_events"] += part["hooks"]
        transcript["stop"], transcript["error"] = part["stop"], part["error"]
        for call in part["calls"]:
            if call["denied"]:
                transcript["tool_calls"].append(
                    {"tool": call["name"], "input": call["raw"], "result": "denied by hook"})
        pending = [c for c in part["calls"] if not c["denied"]]
        if part["error"] or part["stop"] != "tool_use" or not pending:
            return transcript
        results = []
        for call in pending:
            try:
                args = json.loads(call["raw"] or "{}")
            except ValueError:
                args = None
            status, result = execute(store, session_id, call["name"], args, confirm_fn)
            record = {"tool": call["name"], "input": args, "status": status, "result": result}
            transcript["tool_calls"].append(record)
            if on_event:
                on_event(record)
            results.append({"toolResult": {"toolUseId": call["id"],
                                           "content": [{"text": result}], "status": status}})
        messages = [{"role": "user", "content": results}]
    transcript["error"] = "Stopped: too many tool handoffs in one turn."
    return transcript
