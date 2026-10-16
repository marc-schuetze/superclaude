#!/usr/bin/env python3
"""capture.py PORT OUTDIR: fake LLM endpoint for tests/e2e_instructions.sh. Stores every request
body as OUTDIR/<n>.json and answers "ok" (OpenAI chat, OpenAI responses, Anthropic messages)."""
import json, sys, time, itertools
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
seq = itertools.count()

def sse(events):
    return "".join((f"event: {e}\n" if e else "") + f"data: {json.dumps(d) if not isinstance(d, str) else d}\n\n"
                   for e, d in events).encode()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, body, ctype):
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        self.send(json.dumps({"data": [{"id": "capture", "object": "model"}], "models": []}).encode(), "application/json")
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        (out / f"{next(seq):03d}{self.path.replace('/', '_')}.json").write_bytes(raw)
        try: req = json.loads(raw)
        except Exception: req = {}
        stream = req.get("stream", False)
        if self.path.endswith("/messages"):          # anthropic
            ev = [("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "model": "capture", "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}}}),
                  ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                  ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "ok"}}),
                  ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                  ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
                  ("message_stop", {"type": "message_stop"})]
            if stream: return self.send(sse(ev), "text/event-stream")
            return self.send(json.dumps({"id": "m", "type": "message", "role": "assistant", "model": "capture", "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}).encode(), "application/json")
        if self.path.endswith("/responses"):          # openai responses
            item = {"id": "msg", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "ok", "annotations": []}]}
            resp = {"id": "r", "object": "response", "created_at": int(time.time()), "status": "completed", "model": "capture", "output": [item], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
            ev = [("response.created", {"type": "response.created", "response": {**resp, "status": "in_progress", "output": []}}),
                  ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {**item, "status": "in_progress", "content": []}}),
                  ("response.output_text.delta", {"type": "response.output_text.delta", "item_id": "msg", "output_index": 0, "content_index": 0, "delta": "ok"}),
                  ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}),
                  ("response.completed", {"type": "response.completed", "response": resp})]
            return self.send(sse(ev), "text/event-stream")
        # openai chat completions
        if stream:
            ch = lambda d, f: {"id": "c", "object": "chat.completion.chunk", "created": int(time.time()), "model": "capture", "choices": [{"index": 0, "delta": d, "finish_reason": f}]}
            ev = [(None, ch({"role": "assistant", "content": "ok"}, None)), (None, ch({}, "stop")),
                  (None, {"id": "c", "object": "chat.completion.chunk", "created": int(time.time()), "model": "capture", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}),
                  (None, "[DONE]")]
            return self.send(sse(ev), "text/event-stream")
        self.send(json.dumps({"id": "c", "object": "chat.completion", "created": int(time.time()), "model": "capture", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode(), "application/json")

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
