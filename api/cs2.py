"""
Vercel Python — GET /api/cs2?date=YYYY-MM-DD&threshold=0.5
Optional: &refresh=1 to pull PrizePicks / Underdog / Betr immediately.
"""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

_API_DIR = os.path.dirname(os.path.abspath(__file__))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


def _cors():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in _cors().items():
            self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        self._handle(refresh=True)

    def do_GET(self):
        self._handle(refresh=False)

    def _handle(self, refresh: bool):
        out: dict = {"ok": False, "error": "bad_request"}
        try:
            from cs2_impl import get_dashboard

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            date = (qs.get("date") or [None])[0]
            thr = (qs.get("threshold") or ["0.5"])[0]
            try:
                threshold = float(thr)
            except ValueError:
                threshold = 0.5
            force = refresh or str((qs.get("refresh") or [""])[0]).lower() in (
                "1",
                "true",
                "yes",
            )
            out = get_dashboard(date=date, threshold=threshold, refresh=force)
        except Exception as e:
            sys.stderr.write(traceback.format_exc() + "\n")
            out = {"ok": False, "error": "server_error", "detail": str(e)}

        try:
            body = json.dumps(out, default=str).encode("utf-8")
        except Exception as e2:
            body = json.dumps(
                {"ok": False, "error": "json_error", "detail": str(e2)},
                default=str,
            ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for k, v in _cors().items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return
