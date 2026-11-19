#!/usr/bin/env python3
"""Local web demo for InvoiceWarden.

Runs the agent loop locally: a Claude model on Amazon Bedrock (Converse API) proposes
tool calls, and every call goes through the real hook handler (hook/handler.py) and
the real executor (invoicewarden/tools.py) against an in-memory copy of data/seed.json.
Human confirmation happens in the browser; the model never sees that question.

This stands in for the AgentCore harness so the demo needs no deployed stack, only
permission to call one Bedrock model.

Usage:
    AWS_PROFILE=<profile> python demo/server.py                 # http://localhost:8000
    IW_MODEL_ID=<bedrock model or inference profile id> IW_PORT=8000 AWS_REGION=us-east-1
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hook.handler import lambda_handler  # noqa: E402
from invoicewarden.prompts import SYSTEM_PROMPT  # noqa: E402
from invoicewarden.store import MemoryStore  # noqa: E402
from invoicewarden.tools import TOOL_SPECS, execute  # noqa: E402

MODEL_ID = os.environ.get("IW_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
PORT = int(os.environ.get("IW_PORT", "8000"))
MAX_HANDOFFS = 12
CONFIRM_TIMEOUT = 600  # seconds; an unanswered confirmation counts as "declined"
INDEX = Path(__file__).resolve().parent / "index.html"

TOOL_CONFIG = {"tools": [
    {"toolSpec": {"name": t["name"],
                  "description": t["config"]["inlineFunction"]["description"],
                  "inputSchema": {"json": t["config"]["inlineFunction"]["inputSchema"]}}}
    for t in TOOL_SPECS
]}


def _bedrock():
    import boto3
    from botocore.config import Config
    return boto3.client("bedrock-runtime", region_name=REGION,
                        config=Config(read_timeout=300, retries={"max_attempts": 3}))


def _plain(value):
    """Make tool input JSON-safe for the model (Decimal -> float)."""
    return json.loads(json.dumps(value, default=lambda o: float(o) if isinstance(o, Decimal) else str(o)))


class Session:
    def __init__(self, client):
        self.id = f"{uuid.uuid4()}-iw"
        self.client = client
        self.store = MemoryStore()
        self.messages = []
        self.events = []
        self.busy = False
        self.pending = None          # {"id", "tool", "args", "rules", "reason"}
        self._answer = None
        self._answered = threading.Event()
        self.lock = threading.Lock()

    # -- events shown in the browser ----------------------------------------
    def emit(self, kind, **data):
        with self.lock:
            self.events.append({"n": len(self.events), "kind": kind, **_plain(data)})

    # -- human confirmation (called by the executor on CONFIRM) --------------
    def confirm(self, decision, tool, args):
        self._answered.clear()
        self.pending = {"id": uuid.uuid4().hex[:8], "tool": tool, "args": _plain(args),
                        "rules": decision.rules, "reason": decision.reason}
        self.emit("confirm_request", **self.pending)
        got = self._answered.wait(CONFIRM_TIMEOUT)
        approved = bool(got and self._answer)
        self.emit("confirm_answer", approved=approved, timed_out=not got)
        self.pending = None
        return approved

    def answer(self, approved):
        if self.pending:
            self._answer = approved
            self._answered.set()

    # -- one user turn --------------------------------------------------------
    def run_turn(self, text):
        self.busy = True
        try:
            self.emit("user", text=text)
            self.messages.append({"role": "user", "content": [{"text": text}]})
            for _ in range(MAX_HANDOFFS + 1):
                resp = self.client.converse(
                    modelId=MODEL_ID, system=[{"text": SYSTEM_PROMPT}],
                    messages=self.messages, toolConfig=TOOL_CONFIG,
                    inferenceConfig={"maxTokens": 2048})
                msg = resp["output"]["message"]
                self.messages.append(msg)
                for block in msg["content"]:
                    if block.get("text", "").strip():
                        self.emit("agent", text=block["text"])
                calls = [b["toolUse"] for b in msg["content"] if "toolUse" in b]
                if resp.get("stopReason") != "tool_use" or not calls:
                    return
                results = []
                for call in calls:
                    status, result = self._run_tool(call["name"], call.get("input"))
                    results.append({"toolResult": {"toolUseId": call["toolUseId"],
                                                   "content": [{"text": result}], "status": status}})
                self.messages.append({"role": "user", "content": results})
            self.emit("error", text="Stopped: too many tool calls in one turn.")
        except Exception as err:  # noqa: BLE001 - show it in the UI instead of dying silently
            self.emit("error", text=f"{type(err).__name__}: {err}")
            # Drop a dangling user/toolUse turn so the next message starts cleanly.
            while self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            if self.messages and any("toolUse" in b for b in self.messages[-1]["content"]):
                self.messages.pop()
        finally:
            self.busy = False
            self.emit("idle")

    def _run_tool(self, name, args):
        # 1. The before_tool_call hook, exactly as the Lambda would run it.
        hook = lambda_handler({"event": "before_tool_call", "sessionId": self.id,
                               "context": {"toolName": name, "toolInput": args}},
                              None, store=self.store)
        self.emit("hook", tool=name, args=args, decision=hook["decision"], reason=hook.get("reason", ""))
        if hook["decision"] == "deny":
            return "error", f"DENIED by the policy hook: {hook.get('reason')}"
        # 2. The executor: runs the policy again and asks the human when needed.
        status, result = execute(self.store, self.id, name, args, self.confirm)
        self.emit("tool", tool=name, args=args, status=status, result=result)
        return status, result

    def snapshot(self, since):
        with self.lock:
            events = self.events[since:]
        receipts = sorted(self.store.list_receipts(self.id), key=lambda r: r["at"])
        return {"session": self.id, "busy": self.busy, "pending": self.pending,
                "tainted": self.store.is_tainted(self.id), "events": _plain(events),
                "receipts": _plain(receipts), "payments": _plain(self.store.payments),
                "model": MODEL_ID}


SESSIONS: dict[str, Session] = {}
_CLIENT = None


def _session(sid):
    global _CLIENT
    if sid in SESSIONS:
        return SESSIONS[sid]
    _CLIENT = _CLIENT or _bedrock()
    s = Session(_CLIENT)
    SESSIONS[s.id] = s
    return s


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the terminal readable
        if "/api/state" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            data = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/api/state"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            s = SESSIONS.get(q.get("session", [""])[0])
            if not s:
                return self._json(404, {"error": "unknown session"})
            self._json(200, s.snapshot(int(q.get("since", ["0"])[0])))
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        body = self._body()
        if self.path == "/api/session":
            s = _session(None)
            return self._json(200, s.snapshot(0))
        s = SESSIONS.get(body.get("session", ""))
        if not s:
            return self._json(404, {"error": "unknown session"})
        if self.path == "/api/message":
            text = str(body.get("text", "")).strip()[:4000]
            if not text:
                return self._json(400, {"error": "empty message"})
            if s.busy:
                return self._json(409, {"error": "agent is still working"})
            s.busy = True
            threading.Thread(target=s.run_turn, args=(text,), daemon=True).start()
            return self._json(200, {"ok": True})
        if self.path == "/api/confirm":
            s.answer(bool(body.get("approve")))
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})


def main():
    print(f"InvoiceWarden demo on http://localhost:{PORT}  (model {MODEL_ID}, region {REGION}"
          f"{', profile ' + os.environ['AWS_PROFILE'] if os.environ.get('AWS_PROFILE') else ''})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
