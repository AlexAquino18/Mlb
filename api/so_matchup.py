"""GET /api/so_matchup?pitcher_mlbam=123&batters=1,2,3&season=2026 — Statcast arsenal × batter whiff%."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

_API_DIR = os.path.dirname(os.path.abspath(__file__))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from matchup_impl import compute_so_matchup  # noqa: E402


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
        raw_p = (qs.get("pitcher_mlbam") or [None])[0]
        raw_b = (qs.get("batters") or [""])[0]
        raw_season = (qs.get("season") or [None])[0]

        out: dict = {"ok": False, "error": "bad_request"}
        try:
            if not raw_p or not raw_season or not raw_b:
                out = {"ok": False, "error": "missing pitcher_mlbam, batters, or season"}
            else:
                pid = int(raw_p)
                season = int(raw_season)
                bats = [int(x) for x in raw_b.split(",") if x.strip().isdigit()]
                out = compute_so_matchup(pid, bats, season)
        except ValueError:
            out = {"ok": False, "error": "invalid_int"}
        except Exception as e:
            out = {"ok": False, "error": "server_error", "detail": str(e)}

        body = json.dumps(out, default=str).encode("utf-8")
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
