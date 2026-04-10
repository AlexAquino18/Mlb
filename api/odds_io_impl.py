"""
Odds-API.io — fetch MLB events + batched odds (/odds/multi), flatten player-prop rows.
Used by Vercel /api/odds_io and local server.py. API key only via env (never client-side).

Docs: https://docs.odds-api.io/ — base https://api.odds-api.io/v3
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

ODDS_BASE = "https://api.odds-api.io/v3"

# In-server cache to stay under tight hourly quotas (e.g. 100/hr free tiers).
_CACHE: Dict[str, tuple] = {}
CACHE_TTL_SEC = 15 * 60


def _get_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MLB-Edge/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=35) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _events_list(raw: Any) -> List[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        if raw.get("error"):
            return []
        for k in ("data", "events", "results", "items"):
            v = raw.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _multi_list(raw: Any) -> List[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        if raw.get("error"):
            return []
        for k in ("data", "results", "events"):
            v = raw.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _is_mlb_event(ev: dict) -> bool:
    lg = ev.get("league") if isinstance(ev.get("league"), dict) else {}
    slug = (lg.get("slug") or "").lower()
    name = (lg.get("name") or "").lower()
    if slug == "mlb":
        return True
    if "major league" in name and "baseball" in name:
        return True
    # Many feeds tag MLB this way:
    if slug in ("usa-mlb", "us-mlb"):
        return True
    # If league missing, keep baseball events (MiLB risk — rare on main book feeds)
    sp = ev.get("sport") if isinstance(ev.get("sport"), dict) else {}
    if not slug and (sp.get("slug") or "") == "baseball":
        return True
    return False


def _event_date_key(ev: dict) -> str:
    ds = ev.get("date") or ev.get("startTime") or ""
    if isinstance(ds, str) and len(ds) >= 10:
        return ds[:10]
    return ""


def _append_prop_rows(ev: dict, rows: List[dict]) -> None:
    eid = ev.get("id")
    home = ev.get("home") or ""
    away = ev.get("away") or ""
    bookmakers = ev.get("bookmakers") or {}
    if not isinstance(bookmakers, dict):
        return
    for bk, markets in bookmakers.items():
        if not isinstance(markets, list):
            continue
        for m in markets:
            if not isinstance(m, dict):
                continue
            mname = m.get("name") or ""
            for odd in m.get("odds") or []:
                if not isinstance(odd, dict):
                    continue
                label = odd.get("label")
                if not label:
                    continue
                hdp = odd.get("hdp")
                if hdp is None:
                    continue
                try:
                    hf = float(hdp)
                except (TypeError, ValueError):
                    continue
                rows.append(
                    {
                        "eventId": eid,
                        "home": str(home),
                        "away": str(away),
                        "bookmaker": str(bk),
                        "market": str(mname),
                        "player": str(label).strip(),
                        "hdp": hf,
                        "over": odd.get("over"),
                        "under": odd.get("under"),
                    }
                )


def fetch_mlb_odds_bundle(
    api_key: str,
    target_date: str,
    bookmakers: str = "DraftKings,FanDuel",
) -> Dict[str, Any]:
    """
    One events call + ceil(n/10) multi-odds calls. Cached 15 minutes per (date, books).
    """
    date_key = (target_date or "")[:10]
    cache_key = f"{date_key}|{bookmakers}"
    now = time.time()
    if cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts < CACHE_TTL_SEC and data.get("ok"):
            return data

    out: Dict[str, Any] = {
        "ok": False,
        "error": None,
        "rows": [],
        "meta": {"apiCalls": 0, "eventCount": 0, "propRows": 0},
    }
    if len(date_key) != 10:
        out["error"] = "bad_date"
        return out

    try:
        q = urllib.parse.urlencode({"sport": "baseball", "apiKey": api_key})
        events_url = f"{ODDS_BASE}/events?{q}"
        raw_ev = _get_json(events_url)
        out["meta"]["apiCalls"] = 1

        raw_all = _events_list(raw_ev)
        on_date = [e for e in raw_all if _event_date_key(e) == date_key]
        events = [e for e in on_date if _is_mlb_event(e)]
        if not events and on_date:
            events = on_date
        out["meta"]["eventCount"] = len(events)

        rows: List[dict] = []
        if not events:
            out["ok"] = True
            out["meta"]["note"] = "no_mlb_events_for_date"
            out["rows"] = []
            _CACHE[cache_key] = (now, out)
            return out

        event_ids = [e["id"] for e in events if e.get("id") is not None]
        for i in range(0, len(event_ids), 10):
            chunk = event_ids[i : i + 10]
            ids_str = ",".join(str(x) for x in chunk)
            q2 = urllib.parse.urlencode(
                {
                    "apiKey": api_key,
                    "eventIds": ids_str,
                    "bookmakers": bookmakers,
                }
            )
            multi_url = f"{ODDS_BASE}/odds/multi?{q2}"
            multi_raw = _get_json(multi_url)
            out["meta"]["apiCalls"] = out["meta"]["apiCalls"] + 1
            for ev in _multi_list(multi_raw):
                _append_prop_rows(ev, rows)

        out["rows"] = rows
        out["meta"]["propRows"] = len(rows)
        out["ok"] = True
        _CACHE[cache_key] = (now, out)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        out["error"] = f"http_{e.code}: {body[:300]}"
    except Exception as e:
        out["error"] = str(e)

    return out
