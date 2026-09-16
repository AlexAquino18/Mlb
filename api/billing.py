"""
Vercel Python — /api/billing
GET  ?action=me|config|complete|logout
POST {action: checkout|portal|restore, plan?, email?, origin?}
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


def _origin(handler: BaseHTTPRequestHandler, fallback: str = "") -> str:
    host = handler.headers.get("Host") or ""
    proto = handler.headers.get("X-Forwarded-Proto") or ("https" if os.environ.get("VERCEL") else "http")
    if fallback:
        try:
            p = urlparse(fallback)
            if p.netloc and (not host or p.netloc == host):
                return f"{p.scheme}://{p.netloc}"
        except Exception:
            pass
    if host:
        return f"{proto}://{host}"
    return fallback.rstrip("/")


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in _cors().items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def _read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _handle(self, method: str):
        out: dict = {"ok": False, "error": "bad_request"}
        set_cookie = None
        try:
            from billing_impl import (  # noqa: E402
                complete_checkout,
                create_checkout,
                create_portal,
                cookie_header,
                me_from_token,
                parse_cookie,
                public_config,
                restore_email,
            )

            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            action = ((qs.get("action") or [""])[0] or "").lower()
            body = self._read_json() if method == "POST" else {}
            if not action:
                action = str(body.get("action") or "").lower()
            token = parse_cookie(self.headers.get("Cookie") or "")
            origin = _origin(self, str(body.get("origin") or ""))

            if action == "config" or (method == "GET" and not action):
                out = public_config()
            elif action == "me":
                out, tok = me_from_token(token)
                if tok == "":
                    set_cookie = cookie_header("", clear=True)
                elif tok:
                    set_cookie = cookie_header(tok)
            elif action == "logout":
                out = {"ok": True, "plan": None, "enabled": True}
                set_cookie = cookie_header("", clear=True)
            elif action == "complete":
                sid = (qs.get("session_id") or [body.get("session_id") or ""])[0]
                out, tok = complete_checkout(str(sid or ""))
                if tok:
                    set_cookie = cookie_header(tok)
            elif action == "checkout":
                plan = str(body.get("plan") or (qs.get("plan") or [""])[0] or "base")
                me, _ = me_from_token(token)
                out = create_checkout(
                    plan,
                    origin,
                    me.get("customerId") or "",
                    str(body.get("email") or me.get("email") or ""),
                )
            elif action == "portal":
                me, _ = me_from_token(token)
                out = create_portal(me.get("customerId") or "", origin)
            elif action == "restore":
                out, tok = restore_email(str(body.get("email") or ""))
                if tok:
                    set_cookie = cookie_header(tok)
            else:
                out = {"ok": False, "error": "bad_action"}
        except Exception as e:
            sys.stderr.write(traceback.format_exc() + "\n")
            out = {"ok": False, "error": "server_error", "detail": str(e)}

        body_bytes = json.dumps(out, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        for k, v in _cors().items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def log_message(self, format, *args):
        return
