"""mockollama.py - a tiny fake Ollama /api/chat server for offline tests (picks tools by keyword)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs, names = req["messages"], [t["function"]["name"] for t in req["tools"]]
        q = msgs[1]["content"].lower()
        tools = [m for m in msgs if m["role"] == "tool"]
        if not tools:
            want = (["get_battery_history"] if ("drain" in q or "drained" in q) else
                    ["get_thermal_now"] if "overheat" in q else ["get_top_cpu_now"] if "cpu" in q else
                    ["get_recent_errors"] if "crash" in q else
                    ["get_battery_now", "get_thermal_now", "get_top_cpu_now", "get_recent_errors"])
            want = [w for w in want if w in names]
            msg = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": w, "arguments": {}}} for w in want]}
        else:
            msg = {"role": "assistant", "content": "Evidence: " + tools[0]["content"].splitlines()[0] + " Also 99% of nothing."}
        b = json.dumps({"message": msg}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def start():
    srv = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}"
