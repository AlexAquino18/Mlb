"""
Vercel Python — GET /api/odds_io?date=YYYY-MM-DD&bookmakers=DraftKings,FanDuel
Proxies Odds-API.io with ODDS_API_KEY / ODDS_API_IO_KEY from environment only.
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
            from odds_io_impl import fetch_mlb_odds_bundle  # noqa: E402

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            raw_date = (qs.get("date") or [None])[0]
            bookmakers = (qs.get("bookmakers") or ["DraftKings,FanDuel"])[0]
            api_key = os.environ.get("ODDS_API_KEY") or os.environ.get("ODDS_API_IO_KEY")
            if not api_key:
                out = {"ok": False, "error": "missing_ODDS_API_KEY"}
            elif not raw_date:
                out = {"ok": False, "error": "missing_date"}
            else:
                out = fetch_mlb_odds_bundle(api_key, raw_date[:10], bookmakers)
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
        self.send_header("Cache-Control", "public, max-age=900, s-maxage=900")
        for k, v in _cors().items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return
