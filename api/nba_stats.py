"""
Vercel Python — GET /api/nba_stats?date=YYYY-MM-DD
ESPN NBA slate + rosters + game logs + position-vs-team defense.
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
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in _cors().items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        out: dict = {"ok": False, "error": "bad_request"}
        try:
            from nba_stats_impl import get_nba_model

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            date_key = ((qs.get("date") or [""])[0] or "")[:10]
            out = get_nba_model(date_key)
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
        self.send_header("Cache-Control", "public, max-age=600, s-maxage=600")
        for k, v in _cors().items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return
