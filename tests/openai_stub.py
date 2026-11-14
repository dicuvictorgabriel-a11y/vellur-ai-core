#!/usr/bin/env python3
"""OpenAI-compatible PROTOCOL STUB for wiring tests. It is not a model and never pretends to be one.

It answers every request with the fixed text "[protocol stub: no model]" so a test can prove that
requests travel client -> Caddy -> LiteLLM -> backend and back (including streaming), the same
path a real vLLM server on the GPU machine will use. Standard library only.

    python3 openai_stub.py --port 18001 --key stub-key --model stub/backend-model
"""
import argparse, json, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEXT = "[protocol stub: no model]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=18001)
    ap.add_argument("--key", default="stub-key")
    ap.add_argument("--model", default="stub/backend-model")
    ap.add_argument("--agent-script", action="store_true",
                    help="wiring test for coding agents: first ask for one `bash` tool call that creates hello.py, "
                         "then answer with fixed text. Still not a model.")
    a = ap.parse_args()

    class H(BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            if self.headers.get("authorization") != f"Bearer {a.key}":
                self._json(401, {"error": {"message": "bad key", "type": "auth_error"}})
                return False
            return True

        def do_GET(self):
            if not self._authed():
                return
            if self.path.rstrip("/") == "/v1/models":
                return self._json(200, {"object": "list", "data": [{"id": a.model, "object": "model", "owned_by": "stub"}]})
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if not self._authed():
                return
            n = int(self.headers.get("content-length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._json(404, {"error": {"message": "not found"}})
            now, cid = int(time.time()), "chatcmpl-stub"
            tools = [t.get("function", {}).get("name") for t in req.get("tools") or []]
            had_tool = any(m.get("role") == "tool" for m in req.get("messages") or [])
            if a.agent_script and "bash" in tools and not had_tool:
                call = {"index": 0, "id": "call_stub_1", "type": "function", "function": {
                    "name": "bash", "arguments": json.dumps({"command": "printf 'print(\"hello from the wiring test\")\\n' > hello.py",
                                                             "description": "create hello.py"})}}
                if req.get("stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for delta, fin in (({"role": "assistant", "tool_calls": [call]}, None), ({}, "tool_calls")):
                        chunk = {"id": cid, "object": "chat.completion.chunk", "created": now, "model": a.model,
                                 "choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
                    usage = {"id": cid, "object": "chat.completion.chunk", "created": now, "model": a.model, "choices": [],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
                    self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
                    return
                return self._json(200, {"id": cid, "object": "chat.completion", "created": now, "model": a.model,
                                        "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [call]},
                                                     "finish_reason": "tool_calls"}],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
            if req.get("stream"):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for piece in (TEXT[:10], TEXT[10:]):
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": now, "model": a.model,
                             "choices": [{"index": 0, "delta": {"role": "assistant", "content": piece}, "finish_reason": None}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
                end = {"id": cid, "object": "chat.completion.chunk", "created": now, "model": a.model,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())
                return
            self._json(200, {"id": cid, "object": "chat.completion", "created": now, "model": a.model,
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": TEXT}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        def log_message(self, *args):
            pass

    ThreadingHTTPServer((a.host, a.port), H).serve_forever()


if __name__ == "__main__":
    main()
