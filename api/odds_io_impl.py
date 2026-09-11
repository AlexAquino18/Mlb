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
from typing import Any, Dict, List, Optional, Tuple

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


_NFL_TEAM_TOKENS = (
    "cardinals", "arizona", "falcons", "atlanta", "ravens", "baltimore",
    "bills", "buffalo", "panthers", "carolina", "bears", "chicago",
    "bengals", "cincinnati", "browns", "cleveland", "cowboys", "dallas",
    "broncos", "denver", "detroit", "packers", "green bay",
    "texans", "houston texans", "colts", "indianapolis", "jaguars", "jacksonville",
    "chiefs", "kansas city", "raiders", "las vegas", "chargers",
    "rams", "dolphins", "miami", "vikings", "minnesota", "patriots", "new england",
    "saints", "new orleans", "giants", "jets", "eagles", "philadelphia",
    "steelers", "pittsburgh", "49ers", "niners", "san francisco",
    "seahawks", "seattle", "buccaneers", "bucs", "tampa", "titans", "tennessee",
    "commanders", "washington",
)
_NON_NFL_FOOTBALL_MARKERS = (
    "ncaaf", "ncaa", "college", "cfb",
    "cfl", "canadian football", "canada-cfl",
    "roughrider", "stampeder", "elks", "blue bomber", "tiger-cat", "tigercat",
    "argonaut", "alouette", "redblack", "red black", "bc lions", "b.c. lions",
    "b c lions", "british columbia", "saskatchewan", "winnipeg",
    "calgary stamp", "edmonton elk", "hamilton tiger", "ottawa red",
    "montreal alou", "toronto argonaut",
    "ufl", "xfl", "usfl", "aaf", "arena football",
    "roughneck", "battlehawk", "brahma", "showboat", "stallion",
)


def _football_blob(ev: dict) -> str:
    lg = ev.get("league") if isinstance(ev.get("league"), dict) else {}
    return " ".join(
        [
            str(lg.get("slug") or ""),
            str(lg.get("name") or ""),
            _team_str(ev.get("home")),
            _team_str(ev.get("away")),
        ]
    ).lower()


def _looks_like_non_nfl_football(ev: dict) -> bool:
    blob = _football_blob(ev)
    if any(x in blob for x in _NON_NFL_FOOTBALL_MARKERS):
        return True
    # BC Lions must not be treated as Detroit
    if "lions" in blob and ("bc " in blob or "b.c" in blob or "vancouver" in blob):
        return True
    return False


def _looks_like_college_football(ev: dict) -> bool:
    blob = _football_blob(ev)
    return any(x in blob for x in ("ncaaf", "ncaa", "college", "cfb"))


def _team_looks_nfl(name: str) -> bool:
    n = (name or "").lower()
    if not n:
        return False
    if any(x in n for x in _NON_NFL_FOOTBALL_MARKERS):
        return False
    if "lions" in n:
        return "detroit" in n or n.strip() == "lions"
    return any(tok in n for tok in _NFL_TEAM_TOKENS)


def _is_nfl_event(ev: dict) -> bool:
    if _looks_like_non_nfl_football(ev):
        return False
    lg = ev.get("league") if isinstance(ev.get("league"), dict) else {}
    slug = (lg.get("slug") or "").lower()
    name = (lg.get("name") or "").lower()
    if "cfl" in slug or "cfl" in name:
        return False
    if slug.startswith("nfl") or "nfl" in slug or slug in ("usa-nfl", "us-nfl"):
        return True
    if "national football" in name or name.strip() == "nfl" or (
        "nfl" in name and "cfl" not in name
    ):
        return True
    sp = ev.get("sport") if isinstance(ev.get("sport"), dict) else {}
    sp_slug = (sp.get("slug") or "").lower()
    sp_name = (sp.get("name") or "").lower()
    if sp_slug == "nfl" or sp_name == "nfl":
        return True
    home = _team_str(ev.get("home"))
    away = _team_str(ev.get("away"))
    if _team_looks_nfl(home) and _team_looks_nfl(away):
        return True
    return False


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


def _stat_hint_nfl(raw: str) -> str:
    """NFL player-prop labels — more specific phrases first."""
    raw = (raw or "").lower()
    if not raw:
        return ""
    if "anytime" in raw and ("td" in raw or "touchdown" in raw):
        return "anytime_td"
    if (
        ("pass" in raw and "rush" in raw and ("rec" in raw or "receiving" in raw) and "yard" in raw)
        or "pass+rush+rec" in raw
        or "pass + rush + rec" in raw
    ):
        return "pass_rush_rec_yds"
    if ("pass" in raw and "rush" in raw and "yard" in raw) or "pass+rush" in raw or "pass + rush" in raw:
        return "pass_rush_yds"
    if (
        ("rush" in raw and ("rec" in raw or "receiving" in raw) and "yard" in raw)
        or "rush+rec" in raw
        or "rec+rush" in raw
        or "rush + rec" in raw
    ):
        return "rush_rec_yds"
    if ("pass" in raw and "rush" in raw) and ("td" in raw or "touchdown" in raw) and "yard" not in raw:
        return "pass_rush_td"
    if (
        ("rush" in raw and ("rec" in raw or "receiving" in raw))
        and ("td" in raw or "touchdown" in raw)
        and "yard" not in raw
    ):
        return "rush_rec_td"
    if "passing yard" in raw or "pass yard" in raw or "pass yds" in raw or "pass yd" in raw:
        return "pass_yds"
    if "pyards" in raw or raw in ("pass yds", "pass yd", "pyds", "pyd"):
        return "pass_yds"
    if ("passing" in raw or "pass " in raw or raw.startswith("pass")) and (
        "td" in raw or "touchdown" in raw
    ):
        return "pass_td"
    if "completion" in raw or "comp" == raw or "completions" in raw:
        return "completions"
    if ("pass" in raw or "passing" in raw) and "attempt" in raw:
        return "pass_att"
    if "defensive interception" in raw or "def int" in raw:
        return "def_ints"
    if "interception" in raw or raw in ("int", "ints", "ints thrown"):
        return "ints"
    if "rushing yard" in raw or "rush yard" in raw or "rush yds" in raw or "rush yd" in raw:
        return "rush_yds"
    if ("rush" in raw and "attempt" in raw) or "carries" in raw or "carry" in raw:
        return "rush_att"
    if ("rush" in raw) and ("td" in raw or "touchdown" in raw):
        return "rush_td"
    if "receiving yard" in raw or "rec yard" in raw or "rec yds" in raw or "rec yd" in raw:
        return "rec_yds"
    if "reception" in raw or raw in ("recs", "rec", "receptions"):
        return "receptions"
    if ("receiv" in raw or "rec " in raw) and ("td" in raw or "touchdown" in raw):
        return "rec_td"
    if "target" in raw:
        return "targets"
    if "first down" in raw or "1st down" in raw or "1st downs" in raw:
        return "first_downs"
    if "fantasy" in raw:
        return "fantasy"
    if "longest rec" in raw or "long rec" in raw:
        return "long_rec"
    if "longest rush" in raw or "long rush" in raw:
        return "long_rush"
    if "longest pass" in raw or "long pass" in raw or "longest completion" in raw:
        return "long_pass"
    if "sack" in raw:
        return "sacks"
    if "solo tackle" in raw:
        return "solo_tackles"
    if "tackle" in raw and ("assist" in raw or "+" in raw or "combined" in raw):
        return "tackles_ast"
    if "tackle" in raw:
        return "tackles"
    if "longest field" in raw or "long fg" in raw or "fg long" in raw:
        return "fg_long"
    if "field goal" in raw or raw in ("fg", "fgs", "fg made"):
        return "fg_made"
    if "extra point" in raw or "pat made" in raw or raw in ("pat", "xp", "xps"):
        return "pat"
    if "kicking point" in raw or "kicker point" in raw:
        return "kicking_pts"
    return ""


def _stat_hint_from_market(m: dict) -> str:
    """
    Odds-API often sets market.name to only 'Player Props'. Scan other keys/values for stat text.
    """
    try:
        probe = {k: v for k, v in m.items() if k not in ("odds", "bookmakers")}
        blob = json.dumps(probe, default=str).lower()
    except Exception:
        blob = ""
    if not blob:
        return ""
    nfl = _stat_hint_nfl(blob)
    if nfl:
        return nfl
    if ("nrfi" in blob or "yrfi" in blob) and (
        "1st" in blob or "first" in blob or "inning" in blob or "inn" in blob
    ):
        return "nrfi_yrfi"
    if "strikeout" in blob or "strike out" in blob:
        return "strikeouts"
    if "pitcher" in blob and (" k " in blob or "k's" in blob or " ks " in blob):
        return "strikeouts"
    if "total base" in blob:
        return "tb"
    if "home run" in blob:
        return "hr"
    if "rbi" in blob or "runs batted" in blob:
        return "rbi"
    if "stolen" in blob and "base" in blob:
        return "sb"
    if "base on balls" in blob or ("walk" in blob and "pitcher" not in blob):
        return "bb"
    if "hits" in blob and "pitcher" not in blob and "allowed" not in blob and "against" not in blob:
        return "hits"
    if "runs" in blob and ("scored" in blob or "batter" in blob):
        return "runs"
    if "hits+runs" in blob or "h+r+rbi" in blob:
        return "hrr"
    return ""


def _stat_hint_from_text(text: str) -> str:
    raw = (text or "").strip().lower()
    if not raw:
        return ""
    nfl = _stat_hint_nfl(raw)
    if nfl:
        return nfl
    if raw in ("k", "ks", "k's", "k’s"):
        return "strikeouts"
    if "strikeout" in raw or "strike out" in raw:
        return "strikeouts"
    if "total base" in raw:
        return "tb"
    if "home run" in raw:
        return "hr"
    if "rbi" in raw or "runs batted" in raw:
        return "rbi"
    if "stolen" in raw and "base" in raw:
        return "sb"
    if "base on balls" in raw or "walk" in raw:
        return "bb"
    if "hits+runs" in raw or "hits + runs + rbis" in raw or "h+r+rbi" in raw:
        return "hrr"
    if "runs scored" in raw or raw == "runs":
        return "runs"
    if "hits" in raw or raw == "hit":
        return "hits"
    return ""


def _parse_player_label(label: Any) -> Tuple[str, str]:
    raw = str(label or "").strip()
    if not raw:
        return "", ""
    m = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return raw, ""


def _first_player_prop_market(ev: dict) -> Tuple[Optional[str], Optional[str], Optional[dict]]:
    """First (bookmaker, index_str, market) where an odd has a player label."""
    bks = ev.get("bookmakers") or {}
    if not isinstance(bks, dict):
        return None, None, None
    for bk_name, markets in bks.items():
        if not isinstance(markets, list):
            continue
        for mi, m in enumerate(markets):
            if not isinstance(m, dict):
                continue
            for odd in m.get("odds") or []:
                if isinstance(odd, dict) and odd.get("label"):
                    return bk_name, str(mi), m
    return None, None, None


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
                player_name, label_stat = _parse_player_label(label)
                if not player_name:
                    continue
                hdp = odd.get("hdp")
                if hdp is None:
                    continue
                try:
                    hf = float(hdp)
                except (TypeError, ValueError):
                    continue
                mname = _composite_market_name(m, odd)
                label_full = str(label or "")
                hint = (
                    _stat_hint_from_market(m)
                    or _stat_hint_from_text(label_stat)
                    or _stat_hint_from_text(label_full)
                )
                if label_stat and mname.strip().lower() == "player props":
                    mname = f"Player Props · {label_stat}"
                rows.append(
                    {
                        "eventId": eid,
                        "home": str(home),
                        "away": str(away),
                        "bookmaker": str(bk),
                        "market": str(mname),
                        "player": player_name,
                        "hdp": hf,
                        "over": odd.get("over"),
                        "under": odd.get("under"),
                        "statHint": hint,
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
    bk_pp, mi_pp, m_pp = _first_player_prop_market(ev)
    if m_pp is not None:
        odds = m_pp.get("odds") or []
        o0 = odds[0] if odds and isinstance(odds[0], dict) else {}
        out["player_prop_market_example"] = {
            "bookmaker": bk_pp,
            "market_index": mi_pp,
            "name": m_pp.get("name"),
            "market_keys": sorted(m_pp.keys()),
            "stat_hint_guess": _stat_hint_from_market(m_pp),
            "first_odd_keys": sorted(o0.keys()) if isinstance(o0, dict) else [],
            "first_odd_sample": {k: o0.get(k) for k in sorted(o0.keys())[:20]} if isinstance(o0, dict) else o0,
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


# API plan allows max 2 bookmakers (FanDuel, DraftKings). +EV grading vs PrizePicks
# standard lines happens client-side (index.html buildEV).
DEFAULT_BOOKMAKERS = "DraftKings,FanDuel"


def fetch_mlb_odds_bundle(
    api_key: str,
    target_date: str,
    bookmakers: str = DEFAULT_BOOKMAKERS,
    debug_structure: bool = False,
) -> Dict[str, Any]:
    """
    One events call + ceil(n/10) multi-odds calls. Cached 15 minutes per (date, books).
    Pass debug_structure=True to attach meta.oddsStructureSample (not cached).
    """
    date_key = (target_date or "")[:10]
    cache_key = f"{date_key}|{bookmakers}|v19"
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


def _events_in_range(events: List[dict], date_from: str, date_to: str) -> List[dict]:
    if date_from and date_to and date_from == date_to:
        return [e for e in events if _event_date_key(e) == date_from]
    out: List[dict] = []
    for e in events:
        dk = _event_date_key(e)
        if not dk:
            continue
        if date_from and dk < date_from:
            continue
        if date_to and dk > date_to:
            continue
        out.append(e)
    return out


def fetch_nfl_odds_bundle(
    api_key: str,
    date_from: str,
    date_to: str = "",
    bookmakers: str = DEFAULT_BOOKMAKERS,
    debug_structure: bool = False,
) -> Dict[str, Any]:
    """
    NFL player props for a date range (typically Thu–Tue of a given week).
    Odds-API.io sport slug is american-football (nfl is not a valid sport).
    """
    start = (date_from or "")[:10]
    end = (date_to or date_from or "")[:10]
    cache_key = f"nfl|{start}|{end}|{bookmakers}|v5"
    now = time.time()
    if not debug_structure and cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts < CACHE_TTL_SEC and data.get("ok"):
            return data

    out: Dict[str, Any] = {
        "ok": False,
        "error": None,
        "rows": [],
        "meta": {"apiCalls": 0, "eventCount": 0, "propRows": 0, "sport": "nfl"},
    }
    if len(start) != 10 or len(end) != 10:
        out["error"] = "bad_date"
        return out
    if end < start:
        start, end = end, start

    try:
        events: List[dict] = []
        attempts = (
            {"sport": "nfl", "from": start, "to": end},
            {"sport": "american-football", "league": "nfl-regular-season", "from": start, "to": end},
            {"sport": "american-football", "from": start, "to": end},
            {"sport": "nfl"},
            {"sport": "american-football", "league": "nfl-regular-season"},
        )
        for attempt in attempts:
            q_params: Dict[str, str] = {"sport": attempt["sport"], "apiKey": api_key}
            if attempt.get("league"):
                q_params["league"] = str(attempt["league"])
            if attempt.get("from"):
                q_params["from"] = f"{attempt['from']}T00:00:00Z"
            if attempt.get("to"):
                q_params["to"] = f"{attempt['to']}T23:59:59Z"
            events_url = f"{ODDS_BASE}/events?{urllib.parse.urlencode(q_params)}"
            try:
                raw_ev = _get_json(events_url)
            except Exception:
                continue
            out["meta"]["apiCalls"] = out["meta"]["apiCalls"] + 1
            if isinstance(raw_ev, dict) and raw_ev.get("error"):
                continue
            raw_all = _events_list(raw_ev)
            on_range = (
                _events_in_range(raw_all, start, end)
                if attempt.get("from")
                else raw_all
            )
            nfl_events = [e for e in on_range if _is_nfl_event(e)]
            if nfl_events:
                events = nfl_events
                out["meta"]["eventsSport"] = attempt["sport"]
                if attempt.get("league"):
                    out["meta"]["eventsLeague"] = attempt["league"]
                out["meta"]["eventsQuery"] = (
                    "ranged" if attempt.get("from") else "next_14d"
                )
                break
        out["meta"]["eventCount"] = len(events)

        rows: List[dict] = []
        if not events:
            out["ok"] = True
            out["meta"]["note"] = "no_nfl_events_for_range"
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
        out["meta"]["dateFrom"] = start
        out["meta"]["dateTo"] = end
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
                if len(sample_teams) >= 8:
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

