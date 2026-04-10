"""
MLB Edge — Python dev server + PrizePicks proxy
Uses curl_cffi to impersonate Chrome TLS fingerprint, bypassing Cloudflare bot detection.

Install once:
    pip install curl_cffi flask pybaseball
    (or: pip install -r requirements.txt curl_cffi flask)

Run:
    python server.py

Then open: http://localhost:8080
"""

import os
import json
import random
from flask import Flask, request, Response, send_from_directory
from curl_cffi import requests as cffi_requests

app = Flask(__name__, static_folder=".")
PORT = int(os.environ.get("PORT", 8080))
PP_BASE = "https://api.prizepicks.com"

UA_POOL = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization",
}

def proxy_prizepicks(path_param: str):
    target = PP_BASE + path_param
    if not target.startswith(PP_BASE + "/"):
        return Response(json.dumps({"error": "Invalid target"}), status=403,
                        headers={**CORS_HEADERS, "Content-Type": "application/json"})

    last_err = None
    for attempt in range(3):
        ua = random.choice(UA_POOL)
        headers = {
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent":      ua,
            "Referer":         "https://app.prizepicks.com/",
            "Origin":          "https://app.prizepicks.com",
            "X-App-Version":   "9.0.0",
            "Cache-Control":   "no-cache",
            "Pragma":          "no-cache",
        }
        try:
            # impersonate="chrome120" makes curl_cffi mimic Chrome's exact TLS fingerprint
            r = cffi_requests.get(target, headers=headers, impersonate="chrome120", timeout=15)

            if r.status_code in (403, 429) and attempt < 2:
                last_err = f"PrizePicks returned {r.status_code}"
                continue

            return Response(
                r.content,
                status=r.status_code,
                headers={
                    **CORS_HEADERS,
                    "Content-Type":  r.headers.get("content-type", "application/json"),
                    "Cache-Control": "public, max-age=90",
                    "X-PP-Status":   str(r.status_code),
                    "X-PP-Attempt":  str(attempt + 1),
                },
            )
        except Exception as e:
            last_err = str(e)

    return Response(json.dumps({"error": last_err or "Proxy failed"}), status=502,
                    headers={**CORS_HEADERS, "Content-Type": "application/json"})


@app.route("/api/fangraphs_pitcher")
def fangraphs_pitcher_route():
    """FanGraphs SwStr% / CSW% via pybaseball — same contract as Vercel /api/fangraphs_pitcher."""
    if request.method == "OPTIONS":
        return Response(status=204, headers=CORS_HEADERS)
    mlbam = request.args.get("mlbam")
    season = request.args.get("season")
    try:
        import os as _os
        import sys as _sys
        _apid = _os.path.join(_os.path.dirname(__file__), "api")
        if _apid not in _sys.path:
            _sys.path.insert(0, _apid)
        from fangraphs_impl import get_pitcher_advanced
        if not mlbam or not season:
            return Response(
                json.dumps({"ok": False, "error": "missing mlbam or season"}),
                status=400,
                headers={**CORS_HEADERS, "Content-Type": "application/json"},
            )
        out = get_pitcher_advanced(int(mlbam), int(season))
        return Response(
            json.dumps(out, default=str),
            status=200,
            headers={**CORS_HEADERS, "Content-Type": "application/json", "Cache-Control": "public, max-age=3600"},
        )
    except Exception as e:
        # 200 + JSON so the browser can always read { ok: false, detail } (fetch().json works)
        return Response(
            json.dumps({"ok": False, "error": "server_error", "detail": str(e)}),
            status=200,
            headers={**CORS_HEADERS, "Content-Type": "application/json"},
        )


@app.route("/api/batter_advanced")
def batter_advanced_route():
    """Statcast xwOBA / barrels for batters — same contract as Vercel /api/batter_advanced."""
    if request.method == "OPTIONS":
        return Response(status=204, headers=CORS_HEADERS)
    mlbam = request.args.get("mlbam")
    season = request.args.get("season")
    try:
        import os as _os
        import sys as _sys
        _apid = _os.path.join(_os.path.dirname(__file__), "api")
        if _apid not in _sys.path:
            _sys.path.insert(0, _apid)
        from batter_impl import get_batter_advanced
        if not mlbam or not season:
            return Response(
                json.dumps({"ok": False, "error": "missing mlbam or season"}),
                status=400,
                headers={**CORS_HEADERS, "Content-Type": "application/json"},
            )
        out = get_batter_advanced(int(mlbam), int(season))
        return Response(
            json.dumps(out, default=str),
            status=200,
            headers={**CORS_HEADERS, "Content-Type": "application/json", "Cache-Control": "public, max-age=1800"},
        )
    except Exception as e:
        return Response(
            json.dumps({"ok": False, "error": "server_error", "detail": str(e)}),
            status=200,
            headers={**CORS_HEADERS, "Content-Type": "application/json"},
        )


@app.route("/api/so_matchup")
def so_matchup_route():
    """Statcast pitcher mix × batter whiff% — same contract as Vercel /api/so_matchup."""
    if request.method == "OPTIONS":
        return Response(status=204, headers=CORS_HEADERS)
    try:
        import os as _os
        import sys as _sys
        _apid = _os.path.join(_os.path.dirname(__file__), "api")
        if _apid not in _sys.path:
            _sys.path.insert(0, _apid)
        from matchup_impl import compute_so_matchup

        raw_p = request.args.get("pitcher_mlbam")
        raw_b = request.args.get("batters") or ""
        raw_season = request.args.get("season")
        if not raw_p or not raw_season or not raw_b.strip():
            return Response(
                json.dumps({"ok": False, "error": "missing pitcher_mlbam, batters, or season"}),
                status=400,
                headers={**CORS_HEADERS, "Content-Type": "application/json"},
            )
        pid = int(raw_p)
        season = int(raw_season)
        bats = [int(x) for x in raw_b.split(",") if x.strip().isdigit()]
        out = compute_so_matchup(pid, bats, season)
        return Response(
            json.dumps(out, default=str),
            status=200,
            headers={**CORS_HEADERS, "Content-Type": "application/json", "Cache-Control": "public, max-age=900"},
        )
    except ValueError:
        return Response(
            json.dumps({"ok": False, "error": "invalid_int"}),
            status=400,
            headers={**CORS_HEADERS, "Content-Type": "application/json"},
        )
    except Exception as e:
        return Response(
            json.dumps({"ok": False, "error": "server_error", "detail": str(e)}),
            status=200,
            headers={**CORS_HEADERS, "Content-Type": "application/json"},
        )


@app.route("/.netlify/functions/pp")
def pp_proxy():
    if request.method == "OPTIONS":
        return Response(status=204, headers=CORS_HEADERS)
    path_param = request.args.get("path", "")
    if not path_param:
        return Response(json.dumps({"error": "Missing ?path= parameter"}), status=400,
                        headers={**CORS_HEADERS, "Content-Type": "application/json"})
    return proxy_prizepicks(path_param)


@app.route("/api/odds_io")
def odds_io_route():
    """Odds-API.io MLB props — same contract as Vercel /api/odds_io (GET ?date=YYYY-MM-DD)."""
    if request.method == "OPTIONS":
        return Response(status=204, headers=CORS_HEADERS)
    try:
        import os as _os
        import sys as _sys
        _apid = _os.path.join(_os.path.dirname(__file__), "api")
        if _apid not in _sys.path:
            _sys.path.insert(0, _apid)
        from odds_io_impl import fetch_mlb_odds_bundle

        api_key = _os.environ.get("ODDS_API_KEY") or _os.environ.get("ODDS_API_IO_KEY")
        raw_date = request.args.get("date")
        bookmakers = request.args.get("bookmakers") or "DraftKings,FanDuel"
        if not api_key:
            body = {"ok": False, "error": "missing_ODDS_API_KEY"}
        elif not raw_date:
            body = {"ok": False, "error": "missing_date"}
        else:
            body = fetch_mlb_odds_bundle(api_key, raw_date[:10], bookmakers)
        return Response(
            json.dumps(body, default=str),
            status=200,
            headers={**CORS_HEADERS, "Content-Type": "application/json", "Cache-Control": "public, max-age=900"},
        )
    except Exception as e:
        return Response(
            json.dumps({"ok": False, "error": "server_error", "detail": str(e)}),
            status=200,
            headers={**CORS_HEADERS, "Content-Type": "application/json"},
        )


@app.route("/", defaults={"filename": "index.html"})
@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(".", filename)


if __name__ == "__main__":
    print(f"MLB Edge -> http://localhost:{PORT}")
    print("PrizePicks proxy: /.netlify/functions/pp  (Chrome TLS impersonation via curl_cffi)")
    app.run(host="0.0.0.0", port=PORT, debug=False)

# Expose `app` for gunicorn: gunicorn server:app
