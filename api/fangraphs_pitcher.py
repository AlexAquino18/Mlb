"""
Vercel Python — GET /api/fangraphs_pitcher?mlbam=594798&season=2026
Imports fangraphs_impl from the same api/ directory (bundled on deploy).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

_API_DIR = os.path.dirname(os.path.abspath(__file__))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from fangraphs_impl import get_pitcher_advanced  # noqa: E402


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
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        raw_am = (qs.get("mlbam") or [None])[0]
        raw_season = (qs.get("season") or [None])[0]

        out: dict = {"ok": False, "error": "bad_request"}
        status = 200

        try:
            if not raw_am or not raw_season:
                out = {"ok": False, "error": "missing mlbam or season"}
            else:
                mlbam = int(raw_am)
                season = int(raw_season)
                out = get_pitcher_advanced(mlbam, season)
        except ValueError:
            out = {"ok": False, "error": "invalid mlbam or season"}
        except Exception as e:
            out = {"ok": False, "error": "server_error", "detail": str(e)}

        body = json.dumps(out, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=1800, s-maxage=1800")
        for k, v in _cors().items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return
