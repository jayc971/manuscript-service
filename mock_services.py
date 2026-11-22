#!/usr/bin/env python3
"""Mock external services for the take-home. Run: python3 mock_services.py  (port 9000, or PORT=...)
Set SEED=<number> for repeatable behaviour. These services are slow and flaky on purpose."""
import json, os, random, threading, time, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
rng = random.Random(int(os.environ["SEED"])) if "SEED" in os.environ else random.Random()
lock = threading.Lock()
inflight = 0
notify_log = []
keys = set()
def jsend(h, code, obj, headers=None):
    b = json.dumps(obj).encode()
    h.send_response(code); h.send_header("Content-Type", "application/json"); h.send_header("Content-Length", str(len(b)))
    for k, v in (headers or {}).items(): h.send_header(k, v)
    h.end_headers(); h.wfile.write(b)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try: return json.loads(self.rfile.read(n) or b"{}")
        except Exception: return {}
    def do_GET(self):
        if self.path == "/health": return jsend(self, 200, {"ok": True})
        if self.path == "/notify/log":
            with lock:
                sent = [e for e in notify_log if e["result"] == "sent"]
                seen = {}
                for e in sent: seen[(e["to"], e["subject"])] = seen.get((e["to"], e["subject"]), 0) + 1
                dups = [{"to": k[0], "subject": k[1], "times": v} for k, v in seen.items() if v > 1]
                return jsend(self, 200, {"total_requests": len(notify_log), "emails_sent": len(sent), "duplicate_emails": dups, "entries": notify_log})
        jsend(self, 404, {"error": "not found"})
    def do_POST(self):
        global inflight
        if self.path == "/admin/reset":
            with lock: notify_log.clear(); keys.clear()
            return jsend(self, 200, {"reset": True})
        if self.path == "/similarity":
            data = self.body()
            with lock:
                if inflight >= 4: return jsend(self, 429, {"error": "too many concurrent requests"}, {"Retry-After": "2"})
                inflight += 1; fail = rng.random() < 0.25; delay = rng.uniform(1, 3)
            try:
                time.sleep(delay)
                if fail: return jsend(self, 503, {"error": "temporarily unavailable"})
                score = int(hashlib.sha1((data.get("text") or "").encode()).hexdigest(), 16) % 101
                return jsend(self, 200, {"manuscript_id": data.get("manuscript_id"), "score": score})
            finally:
                with lock: inflight -= 1
        if self.path == "/notify":
            data = self.body(); key = self.headers.get("Idempotency-Key")
            with lock:
                entry = {"ts": time.strftime("%H:%M:%S"), "to": data.get("to"), "subject": data.get("subject"), "key": key}
                if key and key in keys:
                    entry["result"] = "duplicate_ignored"; notify_log.append(entry)
                    return jsend(self, 200, {"status": "duplicate_ignored"})
                r = rng.random()
                if r < 0.10:
                    entry["result"] = "failed_before_send"; notify_log.append(entry)
                    return jsend(self, 500, {"error": "internal error"})
                entry["result"] = "sent"; notify_log.append(entry)
                if key: keys.add(key)
                late = r < 0.20
            if late:
                time.sleep(2); return jsend(self, 504, {"error": "gateway timeout"})
            return jsend(self, 200, {"status": "sent"})
        jsend(self, 404, {"error": "not found"})
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 9000))
    print(f"mock services on :{port}"); ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
