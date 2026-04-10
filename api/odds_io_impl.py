"""
Odds-API.io — fetch MLB events + batched odds (/odds/multi), flatten player-prop rows.
Used by Vercel /api/odds_io and local server.py. API key only via env (never client-side).

Docs: https://docs.odds-api.io/ — base https://api.odds-api.io/v3
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

# API metadata often includes ISO timestamps; harvesting all string leaves pollutes composite market names.
_ISO_LIKE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}|^\d{4}-\d{2}-\d{2}$")
_UUID_LIKE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _is_noise_string(s: str) -> bool:
    t = (s or "").strip()
    if not t or len(t) > 400:
        return True
    if _UUID_LIKE.match(t):
        return True
    if _ISO_LIKE.search(t):
        return True
    return False


def _should_skip_harvest_key(k: str) -> bool:
    if not k:
        return False
    kl = k.lower()
    if kl in ("odds", "bookmakers", "id", "eventid", "event_id"):
        return True
    if k.endswith("At") or k.endswith("_at"):
        return True
    if kl in (
        "timestamp",
        "lastupdated",
        "lastmodified",
        "syncedat",
        "starttime",
        "datetime",
    ):
        return True
    # raw ISO date fields often nested under generic names
    if kl in ("date", "time", "ts") and len(k) <= 8:
        return True
    return False

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


def _team_str(v: Any) -> str:
    """Odds API usually returns strings; some feeds use {name, shortName}."""
    if v is None:
        return ""
    if isinstance(v, dict):
        return str(
            v.get("name")
            or v.get("title")
            or v.get("shortName")
            or v.get("label")
            or ""
        ).strip()
    return str(v).strip()


def _eid_key(eid: Any) -> str:
    return str(eid) if eid is not None else ""


def _all_string_values(d: dict, skip: frozenset) -> str:
    """Join string leaf values (excluding metadata keys that are usually ISO timestamps)."""
    parts: List[str] = []
    for k, v in d.items():
        if k in skip or _should_skip_harvest_key(k):
            continue
        if isinstance(v, str) and v.strip():
            if not _is_noise_string(v):
                parts.append(v.strip())
        elif isinstance(v, dict):
            parts.append(_all_string_values(v, skip))
    return " ".join(parts)


def _composite_market_name(m: dict, odd: dict) -> str:
    """Books often set market.name to only 'Player Props'; stat is in other keys."""
    chunks: List[str] = []
    name = (m.get("name") or "").strip()
    if name and not _is_noise_string(name):
        chunks.append(name)
    for key in (
        "title",
        "label",
        "type",
        "category",
        "group",
        "description",
        "handicapName",
        "key",
        "slug",
        "statistic",
        "statType",
        "propType",
        "betType",
        "subType",
    ):
        v = m.get(key)
        if v and str(v).strip():
            sv = str(v).strip()
            if not _is_noise_string(sv):
                chunks.append(sv)
    lbl = str(odd.get("label") or "").strip()
    for key in ("stat", "market", "type", "selectionName", "description", "name"):
        v = odd.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s and s != lbl and not _is_noise_string(s):
            chunks.append(s)
    chunks.append(_all_string_values(m, frozenset({"odds", "bookmakers"})))
    chunks.append(
        _all_string_values(
            odd,
            frozenset({"label", "hdp", "over", "under", "home", "away", "draw"}),
        )
    )
    seen: set = set()
    out: List[str] = []
    for c in chunks:
        if not c or _is_noise_string(c):
            continue
        cl = c.lower().strip()
        if not cl or cl in seen:
            continue
        seen.add(cl)
        out.append(c.strip())
    merged = " · ".join(out)
    return merged if merged.strip() else "Player Props"


def _append_prop_rows(
    ev: dict,
    rows: List[dict],
    event_teams: Dict[str, Tuple[str, str]],
) -> None:
    eid = ev.get("id")
    home = _team_str(ev.get("home"))
    away = _team_str(ev.get("away"))
    # /odds/multi often omits home/away — use /events lookup by id
    ek = _eid_key(eid)
    if ek and event_teams:
        h0, a0 = event_teams.get(ek, ("", ""))
        if not home:
            home = h0
        if not away:
            away = a0
    bookmakers = ev.get("bookmakers") or {}
    if not isinstance(bookmakers, dict):
        return
    for bk, markets in bookmakers.items():
        if not isinstance(markets, list):
            continue
        for m in markets:
            if not isinstance(m, dict):
                continue
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
                mname = _composite_market_name(m, odd)
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


def _debug_trim_event(ev: dict) -> Dict[str, Any]:
    """Small JSON-serializable slice of one /odds/multi event (for ?structure=1 debugging)."""
    out: Dict[str, Any] = {
        "id": ev.get("id"),
        "home": ev.get("home"),
        "away": ev.get("away"),
        "date": ev.get("date"),
        "top_level_keys": sorted(ev.keys())[:80],
    }
    bks = ev.get("bookmakers")
    if not isinstance(bks, dict):
        out["bookmakers"] = f"(not a dict: {type(bks).__name__})"
        return out
    trimmed: Dict[str, Any] = {}
    for bi, (bk_name, markets) in enumerate(bks.items()):
        if bi >= 2:
            break
        if not isinstance(markets, list):
            trimmed[bk_name] = markets
            continue
        tlist: List[Any] = []
        for mi, m in enumerate(markets):
            if mi >= 3:
                break
            if not isinstance(m, dict):
                tlist.append(m)
                continue
            mo: Dict[str, Any] = {
                "name": m.get("name"),
                "market_keys": sorted(m.keys()),
            }
            odds = m.get("odds") or []
            mo["odds_count"] = len(odds) if isinstance(odds, list) else None
            samp: List[Any] = []
            if isinstance(odds, list):
                for oi, odd in enumerate(odds):
                    if oi >= 4:
                        break
                    if isinstance(odd, dict):
                        samp.append({k: odd[k] for k in sorted(odd.keys())})
                    else:
                        samp.append(odd)
            mo["odds_sample"] = samp
            tlist.append(mo)
        trimmed[bk_name] = tlist
    out["bookmakers"] = trimmed
    return out


def fetch_mlb_odds_bundle(
    api_key: str,
    target_date: str,
    bookmakers: str = "DraftKings,FanDuel",
    debug_structure: bool = False,
) -> Dict[str, Any]:
    """
    One events call + ceil(n/10) multi-odds calls. Cached 15 minutes per (date, books).
    Pass debug_structure=True to attach meta.oddsStructureSample (not cached).
    """
    date_key = (target_date or "")[:10]
    cache_key = f"{date_key}|{bookmakers}|v8"
    now = time.time()
    if not debug_structure and cache_key in _CACHE:
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
            if not debug_structure:
                _CACHE[cache_key] = (now, out)
            return out

        event_teams: Dict[str, Tuple[str, str]] = {}
        for e in events:
            eid = e.get("id")
            if eid is None:
                continue
            event_teams[_eid_key(eid)] = (_team_str(e.get("home")), _team_str(e.get("away")))

        event_ids = [e["id"] for e in events if e.get("id") is not None]
        first_multi_raw: Any = None
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
            if debug_structure and first_multi_raw is None:
                first_multi_raw = multi_raw
            out["meta"]["apiCalls"] = out["meta"]["apiCalls"] + 1
            for ev in _multi_list(multi_raw):
                _append_prop_rows(ev, rows, event_teams)

        out["rows"] = rows
        out["meta"]["propRows"] = len(rows)
        sample_markets: List[str] = []
        seen_m: set = set()
        for row in rows:
            m = row.get("market") or ""
            if m and m not in seen_m:
                seen_m.add(m)
                sample_markets.append(m[:160])
                if len(sample_markets) >= 24:
                    break
        out["meta"]["sampleMarkets"] = sample_markets
        sample_teams: List[Dict[str, str]] = []
        seen_t: set = set()
        for row in rows:
            key = (row.get("home"), row.get("away"))
            if key[0] and key not in seen_t:
                seen_t.add(key)
                sample_teams.append({"home": str(key[0]), "away": str(key[1])})
                if len(sample_teams) >= 6:
                    break
        out["meta"]["sampleEventTeams"] = sample_teams
        if debug_structure and first_multi_raw is not None:
            lst = _multi_list(first_multi_raw)
            if lst:
                sj = json.dumps(_debug_trim_event(lst[0]), indent=2, default=str)
                if len(sj) > 36000:
                    sj = sj[:36000] + "\n… (truncated)"
                out["meta"]["oddsStructureSample"] = sj
        out["ok"] = True
        if not debug_structure:
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
