"""
Vercel Python serverless — GET /api/fangraphs_pitcher?mlbam=594798&season=2025
Imports repo-root fangraphs_impl.py (lib/ is not always on Vercel's Python path).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

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
        # Use 200 for almost all JSON responses so the browser does not log hard failures;
        # clients should check `ok`.
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
