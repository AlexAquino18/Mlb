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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

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
THE_ODDS_BASE = "https://api.the-odds-api.com/v4"

# The Odds API market keys → our statHint (FanDuel / DraftKings US books).
_THE_ODDS_MLB_MARKETS = {
    "pitcher_strikeouts": "strikeouts",
    "pitcher_strikeouts_alternate": "strikeouts",
    "pitcher_outs": "pitcher_outs",
    "batter_hits": "hits",
    "batter_total_bases": "tb",
    "batter_home_runs": "hr",
    "batter_rbis": "rbi",
    "batter_runs_scored": "runs",
    "batter_stolen_bases": "sb",
    "batter_hits_runs_rbis": "hrr",
    "batter_walks": "bb",
}
# Keep credit use bounded (1 credit per market per event).
_THE_ODDS_MLB_MARKET_QUERY = (
    "pitcher_strikeouts,pitcher_outs,batter_hits,batter_total_bases,"
    "batter_home_runs,batter_rbis,batter_runs_scored,batter_hits_runs_rbis"
)

# In-server cache to stay under tight hourly quotas (e.g. 100/hr free tiers).
_CACHE: Dict[str, tuple] = {}
CACHE_TTL_SEC = 15 * 60


def _get_json(url: str, user_agent: str = "MLB-Edge/1.0", timeout: int = 18) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _safe_get_json(
    url: str, user_agent: str = "MLB-Edge/1.0", timeout: int = 18
) -> Tuple[Any, Optional[str]]:
    try:
        return _get_json(url, user_agent=user_agent, timeout=timeout), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        return None, f"http_{e.code}: {body[:240]}"
    except Exception as e:
        return None, str(e)[:240]


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


_NON_MLB_BASEBALL = (
    "npb",
    "kbo",
    "cpbl",
    "milb",
    "ncaa",
    "college",
    "minor league",
    "triple-a",
    "double-a",
    "mexican league",
    "nippon",
    "wbsc",
    "independent",
    "kbo-korea",
)


def _is_mlb_event(ev: dict) -> bool:
    lg = ev.get("league") if isinstance(ev.get("league"), dict) else {}
    slug = (lg.get("slug") or "").lower()
    name = (lg.get("name") or "").lower()
    sp = ev.get("sport") if isinstance(ev.get("sport"), dict) else {}
    sp_slug = (sp.get("slug") or "").lower()
    sp_name = (sp.get("name") or "").lower()
    blob = " ".join([slug, name, sp_slug, sp_name])
    if any(tok in blob for tok in _NON_MLB_BASEBALL):
        return False
    if "mlb" in slug or slug in ("usa-mlb", "us-mlb"):
        return True
    if "mlb" in name:
        return True
    if "major league" in name and "baseball" in name:
        return True
    if sp_slug == "mlb" or sp_name == "mlb":
        return True
    # If league missing, keep baseball events (MiLB risk — rare on main book feeds)
    if not slug and sp_slug == "baseball":
        return True
    return False


def _event_date_key(ev: dict) -> str:
    dates = _event_date_keys(ev)
    return sorted(dates)[0] if dates else ""


def _event_date_keys(ev: dict) -> List[str]:
    ds = ev.get("date") or ev.get("startTime") or ""
    if not isinstance(ds, str) or len(ds) < 10:
        return []
    keys = {ds[:10]}
    try:
        dt = datetime.fromisoformat(ds.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        keys.add(dt.astimezone(ZoneInfo("America/New_York")).date().isoformat())
        keys.add(dt.astimezone(timezone.utc).date().isoformat())
    except Exception:
        pass
    return sorted(keys)


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


def _et_rfc3339_bounds(date_key: str) -> Tuple[str, str]:
    """UTC window covering an America/New_York calendar day, with a few hours of pad."""
    et = ZoneInfo("America/New_York")
    start = datetime.fromisoformat(f"{date_key}T00:00:00").replace(tzinfo=et) - timedelta(hours=6)
    end = datetime.fromisoformat(f"{date_key}T23:59:59").replace(tzinfo=et) + timedelta(hours=6)
    return (
        start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _bookmakers_map(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    out: Dict[str, Any] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(
                item.get("name")
                or item.get("bookmaker")
                or item.get("title")
                or item.get("key")
                or ""
            ).strip()
            markets = item.get("markets")
            if markets is None and item.get("odds") is not None:
                markets = [
                    {
                        "name": item.get("market") or item.get("marketName") or "Player Props",
                        "odds": item.get("odds"),
                    }
                ]
            if name and markets is not None:
                out[name] = markets
    return out


def _markets_as_list(raw: Any) -> List[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        out: List[dict] = []
        for k, v in raw.items():
            if isinstance(v, dict):
                m = dict(v)
                if not m.get("name"):
                    m["name"] = str(k)
                out.append(m)
            elif isinstance(v, list):
                out.append({"name": str(k), "odds": v})
        return out
    return []


def _odds_as_list(raw: Any) -> List[dict]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        if any(k in raw for k in ("over", "under", "label", "hdp", "home", "away")):
            return [raw]
        return [v for v in raw.values() if isinstance(v, dict)]
    return []


def _flatten_price(v: Any) -> Optional[str]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        s = v.strip()
        return s or None
    if isinstance(v, dict):
        for k in ("american", "decimal", "odds", "price", "value"):
            got = _flatten_price(v.get(k))
            if got:
                return got
    return None


def _odd_over_under(odd: dict) -> Tuple[Optional[str], Optional[str]]:
    over = (
        _flatten_price(odd.get("over"))
        or _flatten_price(odd.get("overOdds"))
        or _flatten_price(odd.get("overPrice"))
        or _flatten_price(odd.get("o"))
    )
    under = (
        _flatten_price(odd.get("under"))
        or _flatten_price(odd.get("underOdds"))
        or _flatten_price(odd.get("underPrice"))
        or _flatten_price(odd.get("u"))
    )
    side = " ".join(
        [
            str(odd.get("betSide") or ""),
            str(odd.get("side") or ""),
            str(odd.get("name") or ""),
            str(odd.get("selection") or ""),
            str(odd.get("outcome") or ""),
        ]
    ).lower()
    price = _flatten_price(
        odd.get("odds") or odd.get("price") or odd.get("american") or odd.get("decimal")
    )
    if price:
        if "under" in side and "over" not in side and not under:
            under = price
        elif "over" in side and not over:
            over = price
    return over, under


def _norm_bookmaker(name: str) -> str:
    n = (name or "").strip()
    low = n.lower()
    if "fanduel" in low:
        return "FanDuel"
    if "draftking" in low:
        return "DraftKings"
    return n


def _odd_line(odd: dict, label: str = "") -> Optional[float]:
    for k in ("hdp", "line", "handicap", "spread", "points", "max"):
        v = odd.get(k)
        if v is None or v == "":
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        if k == "max" and n > 30:
            continue
        return n
    raw = str(label or "")
    m = re.search(
        r"(?:over|under)\s+(-?\d+\.?\d*)(?:\s*(?:k(?:s|'s)?|strikeouts?|hits?|total bases?))?\s*$",
        raw,
        re.I,
    )
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


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


def _stat_hint_from_market(m: dict, sport: str = "") -> str:
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
    # NFL keyword scan on a full market JSON blob false-positives MLB strikeout markets.
    if sport != "mlb":
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
    if raw in ("k", "ks", "k's", "k’s", "so", "pitcher k", "pitcher ks", "pitcher k's", "pitcher k’s"):
        return "strikeouts"
    if "strikeout" in raw or "strike out" in raw:
        return "strikeouts"
    if "pitcher" in raw and re.search(r"\bk'?s?\b", raw):
        return "strikeouts"
    nfl = _stat_hint_nfl(raw)
    if nfl:
        return nfl
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
    # Odds-API docs: "Blake Snell - Strikeouts" as well as "Name (Stat)".
    for sep in (" - ", " – ", " — "):
        if sep not in raw:
            continue
        left, right = raw.rsplit(sep, 1)
        left, right = left.strip(), right.strip()
        if left and _stat_hint_from_text(right):
            return left, right
        break
    # "Blake Snell Over 7.5 Strikeouts"
    m2 = re.match(r"^(.*?)\s+(over|under)\s+(-?\d+\.?\d*)\s*(.*)$", raw, re.I)
    if m2 and m2.group(1).strip():
        rest = (m2.group(4) or "").strip()
        return m2.group(1).strip(), rest or m2.group(2)
    return raw, ""


def _first_player_prop_market(ev: dict) -> Tuple[Optional[str], Optional[str], Optional[dict]]:
    """First (bookmaker, index_str, market) where an odd has a player label."""
    bks = _bookmakers_map(ev.get("bookmakers"))
    if not bks:
        return None, None, None
    for bk_name, markets in bks.items():
        for mi, m in enumerate(_markets_as_list(markets)):
            for odd in _odds_as_list(m.get("odds")):
                if odd.get("label") or odd.get("player"):
                    return bk_name, str(mi), m
    return None, None, None


def _append_prop_rows(
    ev: dict,
    rows: List[dict],
    event_teams: Dict[str, Tuple[str, str]],
    sport: str = "",
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
    bookmakers = _bookmakers_map(ev.get("bookmakers"))
    if not bookmakers:
        return
    for bk, markets in bookmakers.items():
        bk_name = _norm_bookmaker(str(bk))
        for m in _markets_as_list(markets):
            mname_raw = str(m.get("name") or "")
            if "correct score" in mname_raw.lower():
                continue
            for odd in _odds_as_list(m.get("odds")):
                label = (
                    odd.get("label")
                    or odd.get("player")
                    or odd.get("participant")
                    or m.get("label")
                    or m.get("player")
                )
                name_field = str(odd.get("name") or "").strip()
                if not label and name_field.lower() not in ("over", "under", "yes", "no"):
                    label = name_field
                if not label:
                    continue
                player_name, label_stat = _parse_player_label(label)
                if not player_name:
                    continue
                if not re.search(r"[A-Za-z]", player_name):
                    continue
                if re.match(r"^\d", player_name.strip()):
                    continue
                hdp = _odd_line(odd, str(label))
                if hdp is None:
                    hdp = _odd_line(m, str(label))
                if hdp is None:
                    continue
                try:
                    hf = float(hdp)
                except (TypeError, ValueError):
                    continue
                over, under = _odd_over_under(odd)
                mname = _composite_market_name(m, odd)
                label_full = str(label or "")
                hint = (
                    _stat_hint_from_text(label_stat)
                    or _stat_hint_from_text(label_full)
                    or _stat_hint_from_text(mname_raw)
                    or _stat_hint_from_market(m, sport=sport)
                )
                mlow = mname.strip().lower()
                if label_stat and mlow in ("player props", "player prop"):
                    mname = f"Player Props · {label_stat}"
                elif label_stat and "player props" in mlow and label_stat.lower() not in mname.lower():
                    mname = f"{mname} · {label_stat}"
                rows.append(
                    {
                        "eventId": eid,
                        "home": str(home),
                        "away": str(away),
                        "bookmaker": bk_name,
                        "market": str(mname),
                        "player": player_name,
                        "hdp": hf,
                        "over": over,
                        "under": under,
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
            "stat_hint_guess": _stat_hint_from_market(m_pp, sport="mlb"),
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


def _odds_multi_rows(
    api_key: str,
    event_ids: List[Any],
    bookmakers: str,
    event_teams: Dict[str, Tuple[str, str]],
    out: Dict[str, Any],
    sport: str,
    markets: Optional[str] = None,
    debug_structure: bool = False,
) -> Tuple[List[dict], Any]:
    rows: List[dict] = []
    first_raw: Any = None
    for i in range(0, len(event_ids), 10):
        chunk = event_ids[i : i + 10]
        ids_str = ",".join(str(x) for x in chunk)
        params: Dict[str, str] = {
            "apiKey": api_key,
            "eventIds": ids_str,
        }
        if bookmakers:
            params["bookmakers"] = bookmakers
        if markets:
            params["markets"] = markets
        multi_url = f"{ODDS_BASE}/odds/multi?{urllib.parse.urlencode(params)}"
        multi_raw, err = _safe_get_json(multi_url)
        out["meta"]["apiCalls"] = out["meta"].get("apiCalls", 0) + 1
        if err:
            out["meta"].setdefault("multiErrors", []).append(err)
            continue
        if debug_structure and first_raw is None:
            first_raw = multi_raw
        for ev in _multi_list(multi_raw):
            _append_prop_rows(ev, rows, event_teams, sport=sport)
    return rows, first_raw


AN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
# Action Network book IDs: 68 = DraftKings NJ, 69 = FanDuel NJ (national US feeds).
AN_BOOK_IDS = {68: "DraftKings", 69: "FanDuel"}
AN_MLB_SCOREBOARD = "https://api.actionnetwork.com/web/v2/scoreboard/mlb"
AN_NFL_SCOREBOARD = "https://api.actionnetwork.com/web/v2/scoreboard/nfl"
AN_MARKET_MAP = {
    "core_bet_type_37_strikeouts": ("strikeouts", "Pitcher Strikeouts"),
    "core_bet_type_42_pitching_outs": ("pitcher_outs", "Pitching Outs"),
    "core_bet_type_36_hits": ("hits", "Hits"),
    "core_bet_type_34_rbi": ("rbi", "RBI"),
    "core_bet_type_33_hr": ("hr", "Home Runs"),
}
AN_NFL_BATCHES = [
    {
        "core_bet_type_9_passing_yards": ("pass_yds", "Passing Yards"),
        "core_bet_type_10_pass_completions": ("completions", "Completions"),
        "core_bet_type_12_rushing_yards": ("rush_yds", "Rushing Yards"),
        "core_bet_type_15_receptions": ("receptions", "Receptions"),
        "core_bet_type_16_receiving_yards": ("rec_yds", "Receiving Yards"),
        "core_bet_type_18_rushing_attempts": ("rush_att", "Rush Attempts"),
        "core_bet_type_30_passing_attempts": ("pass_att", "Pass Attempts"),
    },
    {
        "core_bet_type_11_passing_tds": ("pass_td", "Passing TDs"),
        "core_bet_type_13_rushing_tds": ("rush_td", "Rushing TDs"),
        "core_bet_type_17_receiving_tds": ("rec_td", "Receiving TDs"),
        "core_bet_type_62_anytime_touchdown_scorer": ("anytime_td", "Anytime TD"),
        "core_bet_type_65_interceptions": ("ints", "Interceptions"),
        "core_bet_type_66_rushing_receiving_yards": ("rush_rec_yds", "Rush+Rec Yds"),
        "core_bet_type_71_passing_rushing_yards": ("pass_rush_yds", "Pass+Rush Yds"),
        "core_bet_type_43_kicking_points": ("kicking_pts", "Kicking Pts"),
        "core_bet_type_213_field_goals_made": ("fg_made", "FG Made"),
        "core_bet_type_578_sacks": ("sacks", "Sacks"),
    },
]


def _an_game_in_range(g: dict, date_from: str, date_to: str) -> bool:
    start = str(g.get("start_time") or "")
    keys = _event_date_keys({"date": start})
    if not keys:
        return True
    lo = (date_from or "")[:10]
    hi = (date_to or date_from or "")[:10]
    if lo and hi and hi != lo:
        return any(lo <= d <= hi for d in keys)
    if lo:
        return lo in keys
    return True


def _an_team_names(g: dict) -> Tuple[str, str]:
    home = away = ""
    teams = g.get("teams") or []
    if not isinstance(teams, list):
        return home, away
    hid = g.get("home_team_id")
    aid = g.get("away_team_id")
    by_id = {t.get("id"): t for t in teams if isinstance(t, dict)}

    def _tn(t: Any) -> str:
        if not isinstance(t, dict):
            return ""
        return str(t.get("full_name") or t.get("display_name") or t.get("name") or "").strip()

    if hid in by_id:
        home = _tn(by_id[hid])
    if aid in by_id:
        away = _tn(by_id[aid])
    if (not home or not away) and len(teams) >= 2:
        if not away:
            away = _tn(teams[0])
        if not home:
            home = _tn(teams[1])
    return home, away


def _an_rows_from_payload(
    raw: Any,
    market_map: Dict[str, Tuple[str, str]],
    date_from: str,
    date_to: str,
) -> List[dict]:
    if not isinstance(raw, dict):
        return []
    games = raw.get("games") or []
    players: Dict[Any, str] = {}
    for g in games:
        if not isinstance(g, dict):
            continue
        for p in g.get("players") or []:
            if isinstance(p, dict) and p.get("id") is not None:
                name = str(p.get("full_name") or p.get("preferred_name") or "").strip()
                if name:
                    players[p["id"]] = name
    rows: List[dict] = []
    for g in games:
        if not isinstance(g, dict):
            continue
        if not _an_game_in_range(g, date_from, date_to):
            continue
        home, away = _an_team_names(g)
        markets = g.get("markets") or {}
        if not isinstance(markets, dict):
            continue
        bucket: Dict[Tuple[Any, ...], dict] = {}
        for bid_s, mk in markets.items():
            try:
                bid = int(bid_s)
            except (TypeError, ValueError):
                continue
            bname = AN_BOOK_IDS.get(bid)
            if not bname or not isinstance(mk, dict):
                continue
            event_mk = mk.get("event") if isinstance(mk.get("event"), dict) else mk
            if not isinstance(event_mk, dict):
                continue
            for mkey, outcomes in event_mk.items():
                hint_pair = market_map.get(str(mkey))
                if not hint_pair or not isinstance(outcomes, list):
                    continue
                hint, mlabel = hint_pair
                for o in outcomes:
                    if not isinstance(o, dict) or o.get("is_alt_market"):
                        continue
                    player = players.get(o.get("player_id")) or ""
                    if not player:
                        continue
                    try:
                        hf = float(o.get("value"))
                    except (TypeError, ValueError):
                        continue
                    side = str(o.get("side") or "").lower()
                    price = _flatten_price(o.get("odds"))
                    key = (player, bname, hint, hf)
                    rec = bucket.setdefault(
                        key,
                        {
                            "eventId": g.get("id"),
                            "home": home,
                            "away": away,
                            "bookmaker": bname,
                            "market": mlabel,
                            "player": player,
                            "hdp": hf,
                            "over": None,
                            "under": None,
                            "statHint": hint,
                        },
                    )
                    if side == "over":
                        rec["over"] = price
                    elif side == "under":
                        rec["under"] = price
        rows.extend(
            rec
            for rec in bucket.values()
            if rec.get("over") not in (None, "") or rec.get("under") not in (None, "")
        )
    return rows


def _fetch_actionnetwork_scoreboard(
    scoreboard: str,
    market_map: Dict[str, Tuple[str, str]],
    date_from: str,
    date_to: str,
    out: Dict[str, Any],
    timeout: int = 25,
) -> List[dict]:
    pick_types = ",".join(market_map.keys())
    book_ids = ",".join(str(i) for i in AN_BOOK_IDS)
    q = urllib.parse.urlencode(
        {"customPickTypes": pick_types, "bookIds": book_ids},
        safe=",",
    )
    url = f"{scoreboard}?{q}"
    raw, err = _safe_get_json(url, user_agent=AN_UA, timeout=timeout)
    out["meta"]["apiCalls"] = out["meta"].get("apiCalls", 0) + 1
    if err:
        out["meta"].setdefault("actionNetworkErrors", []).append(err)
        return []
    return _an_rows_from_payload(raw, market_map, date_from, date_to)


def fetch_actionnetwork_mlb(date_key: str, out: Dict[str, Any]) -> List[dict]:
    """FanDuel / DraftKings MLB player props from Action Network (no API key)."""
    rows = _fetch_actionnetwork_scoreboard(
        AN_MLB_SCOREBOARD, AN_MARKET_MAP, date_key, date_key, out, timeout=22
    )
    out["meta"]["actionNetworkRows"] = len(rows)
    return rows


def fetch_actionnetwork_nfl(date_from: str, date_to: str, out: Dict[str, Any]) -> List[dict]:
    """FanDuel / DraftKings NFL player props from Action Network (no API key)."""
    rows: List[dict] = []
    seen: set = set()
    for batch in AN_NFL_BATCHES:
        part = _fetch_actionnetwork_scoreboard(
            AN_NFL_SCOREBOARD, batch, date_from, date_to, out, timeout=28
        )
        for rec in part:
            key = (rec.get("player"), rec.get("bookmaker"), rec.get("statHint"), rec.get("hdp"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(rec)
    out["meta"]["actionNetworkRows"] = len(rows)
    return rows


def _the_odds_event_to_rows(ev: dict) -> List[dict]:
    home = str(ev.get("home_team") or "")
    away = str(ev.get("away_team") or "")
    eid = ev.get("id")
    bucket: Dict[Tuple[Any, ...], dict] = {}
    for bk in ev.get("bookmakers") or []:
        if not isinstance(bk, dict):
            continue
        bname = _norm_bookmaker(str(bk.get("title") or bk.get("key") or ""))
        if bname not in ("FanDuel", "DraftKings"):
            continue
        for m in bk.get("markets") or []:
            if not isinstance(m, dict):
                continue
            mkey = str(m.get("key") or "")
            hint = _THE_ODDS_MLB_MARKETS.get(mkey, "")
            mlabel = mkey.replace("_", " ").title() or "Player Props"
            for o in m.get("outcomes") or []:
                if not isinstance(o, dict):
                    continue
                player = str(o.get("description") or "").strip()
                if not player:
                    continue
                try:
                    hf = float(o.get("point"))
                except (TypeError, ValueError):
                    continue
                side = str(o.get("name") or "").strip().lower()
                price = _flatten_price(o.get("price"))
                key = (player, bname, mkey, hf)
                rec = bucket.setdefault(
                    key,
                    {
                        "eventId": eid,
                        "home": home,
                        "away": away,
                        "bookmaker": bname,
                        "market": mlabel,
                        "player": player,
                        "hdp": hf,
                        "over": None,
                        "under": None,
                        "statHint": hint,
                    },
                )
                if side == "over":
                    rec["over"] = price
                elif side == "under":
                    rec["under"] = price
    return list(bucket.values())


def _the_odds_key_works(api_key: str) -> Tuple[bool, Optional[str]]:
    url = f"{THE_ODDS_BASE}/sports/?{urllib.parse.urlencode({'apiKey': api_key})}"
    data, err = _safe_get_json(url)
    if isinstance(data, list) and data:
        return True, None
    if isinstance(data, dict):
        msg = data.get("message") or data.get("error_msg") or data.get("error")
        if msg:
            return False, str(msg)[:220]
    return False, err


def fetch_the_odds_mlb(api_key: str, date_key: str, out: Dict[str, Any]) -> List[dict]:
    """FanDuel / DraftKings MLB player props via The Odds API (the-odds-api.com)."""
    from_s, to_s = _et_rfc3339_bounds(date_key)
    q = urllib.parse.urlencode(
        {
            "apiKey": api_key,
            "commenceTimeFrom": from_s,
            "commenceTimeTo": to_s,
        }
    )
    events_url = f"{THE_ODDS_BASE}/sports/baseball_mlb/events?{q}"
    raw_ev, err = _safe_get_json(events_url)
    out["meta"]["apiCalls"] = out["meta"].get("apiCalls", 0) + 1
    if err:
        out["meta"]["theOddsError"] = err
        return []
    if isinstance(raw_ev, dict):
        msg = raw_ev.get("message") or raw_ev.get("error_msg") or raw_ev.get("error")
        if msg:
            out["meta"]["theOddsError"] = str(msg)[:220]
            return []
        raw_ev = raw_ev.get("data") or []
    if not isinstance(raw_ev, list):
        out["meta"]["theOddsError"] = "bad_events_payload"
        return []
    events = []
    for e in raw_ev:
        if not isinstance(e, dict):
            continue
        fake = {"date": e.get("commence_time") or ""}
        if date_key in _event_date_keys(fake) or not _event_date_keys(fake):
            events.append(e)
    if not events:
        events = [e for e in raw_ev if isinstance(e, dict)]
    out["meta"]["theOddsEvents"] = len(events)
    if not events:
        return []

    def _one(ev: dict) -> List[dict]:
        eid = ev.get("id")
        if not eid:
            return []
        q2 = urllib.parse.urlencode(
            {
                "apiKey": api_key,
                "regions": "us",
                "markets": _THE_ODDS_MLB_MARKET_QUERY,
                "oddsFormat": "american",
                "bookmakers": "fanduel,draftkings",
            }
        )
        odds_url = f"{THE_ODDS_BASE}/sports/baseball_mlb/events/{eid}/odds?{q2}"
        data, e2 = _safe_get_json(odds_url)
        if e2 or not isinstance(data, dict):
            if e2:
                out["meta"].setdefault("theOddsOddsErrors", []).append(str(e2)[:160])
            return []
        data.setdefault("home_team", ev.get("home_team"))
        data.setdefault("away_team", ev.get("away_team"))
        data.setdefault("id", eid)
        return _the_odds_event_to_rows(data)

    rows: List[dict] = []
    workers = min(6, max(1, len(events)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, ev) for ev in events]
        for fut in as_completed(futs):
            try:
                rows.extend(fut.result() or [])
            except Exception as ex:
                out["meta"].setdefault("theOddsOddsErrors", []).append(str(ex)[:160])
    out["meta"]["apiCalls"] = out["meta"].get("apiCalls", 0) + len(events)
    out["meta"]["theOddsRows"] = len(rows)
    return rows


def _has_fd_dk_priced(rows: List[dict], stat_hint: Optional[str] = None) -> bool:
    fd = dk = False
    for r in rows:
        if stat_hint and (r.get("statHint") or "") != stat_hint:
            continue
        if r.get("over") in (None, "") and r.get("under") in (None, ""):
            continue
        if r.get("bookmaker") == "FanDuel":
            fd = True
        elif r.get("bookmaker") == "DraftKings":
            dk = True
    return fd and dk


def _has_fd_dk_strikeouts(rows: List[dict]) -> bool:
    return _has_fd_dk_priced(rows, "strikeouts")


def fetch_mlb_odds_bundle(
    api_key: str,
    target_date: str,
    bookmakers: str = DEFAULT_BOOKMAKERS,
    debug_structure: bool = False,
    the_odds_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    FanDuel / DraftKings MLB player props.
    Action Network (no key) first, then The Odds API / Odds-API.io if needed.
    """
    date_key = (target_date or "")[:10]
    cache_key = f"{date_key}|{bookmakers}|v26"
    now = time.time()
    if not debug_structure and cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        ttl = CACHE_TTL_SEC if data.get("rows") else 90
        if not data.get("ok"):
            ttl = 30
        if now - ts < ttl:
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

    toa_key = (the_odds_key or api_key or "").strip()
    io_key = (api_key or "").strip()

    try:
        rows: List[dict] = []
        first_multi_raw: Any = None

        rows.extend(fetch_actionnetwork_mlb(date_key, out))
        skip_paid = _has_fd_dk_strikeouts(rows)
        out["meta"]["skippedPaidOdds"] = skip_paid

        if not skip_paid and toa_key:
            ok_toa, toa_probe_err = _the_odds_key_works(toa_key)
            out["meta"]["apiCalls"] = out["meta"].get("apiCalls", 0) + 1
            out["meta"]["theOddsKeyOk"] = ok_toa
            if ok_toa:
                rows.extend(fetch_the_odds_mlb(toa_key, date_key, out))
            elif toa_probe_err:
                out["meta"]["theOddsError"] = toa_probe_err

        skip_io = skip_paid or _has_fd_dk_strikeouts(rows)
        out["meta"]["skippedOddsIo"] = skip_io

        events: List[dict] = []
        seen_ids: set = set()
        if not skip_io and io_key:
            from_s, to_s = _et_rfc3339_bounds(date_key)
            q_params: Dict[str, str] = {
                "sport": "baseball",
                "apiKey": io_key,
                "from": from_s,
                "to": to_s,
                "limit": "80",
            }
            events_url = f"{ODDS_BASE}/events?{urllib.parse.urlencode(q_params)}"
            raw_ev, ev_err = _safe_get_json(events_url)
            out["meta"]["apiCalls"] = out["meta"].get("apiCalls", 0) + 1
            if ev_err:
                out["meta"].setdefault("eventErrors", []).append(ev_err)
                raw_ev = None
            if isinstance(raw_ev, dict) and raw_ev.get("error"):
                out["meta"].setdefault("eventErrors", []).append(str(raw_ev.get("error")))
                raw_ev = None
            raw_all = _events_list(raw_ev)
            mlb_all = [e for e in raw_all if _is_mlb_event(e)]
            on_date = [e for e in mlb_all if date_key in _event_date_keys(e)]
            pool = on_date or mlb_all
            for e in pool:
                eid = _eid_key(e.get("id"))
                if not eid or eid in seen_ids:
                    continue
                seen_ids.add(eid)
                events.append(e)
            out["meta"]["eventsSport"] = "baseball"
            out["meta"]["eventCount"] = len(events)

            if events:
                event_teams: Dict[str, Tuple[str, str]] = {}
                for e in events:
                    eid = e.get("id")
                    if eid is None:
                        continue
                    event_teams[_eid_key(eid)] = (_team_str(e.get("home")), _team_str(e.get("away")))

                event_ids = [e["id"] for e in events if e.get("id") is not None]
                io_rows, first_multi_raw = _odds_multi_rows(
                    io_key,
                    event_ids,
                    bookmakers,
                    event_teams,
                    out,
                    sport="mlb",
                    markets=None,
                    debug_structure=debug_structure,
                )
                errs = out["meta"].get("multiErrors") or []
                quota_hit = any("http_429" in str(x) or "http_403" in str(x) for x in errs)
                if not io_rows and event_ids and not quota_hit:
                    extra, first2 = _odds_multi_rows(
                        io_key,
                        event_ids,
                        "",
                        event_teams,
                        out,
                        sport="mlb",
                        markets=None,
                        debug_structure=debug_structure,
                    )
                    if extra:
                        io_rows = extra
                        if first_multi_raw is None:
                            first_multi_raw = first2
                        out["meta"]["multiFallback"] = "all_bookmakers"
                rows.extend(io_rows or [])

        if not events and not rows:
            out["meta"]["note"] = "no_mlb_events_for_date"

        out["rows"] = rows
        out["meta"]["propRows"] = len(rows)
        out["meta"]["bookmakersSeen"] = sorted(
            {str(r.get("bookmaker") or "") for r in rows if r.get("bookmaker")}
        )
        out["meta"]["pricedRows"] = sum(
            1 for r in rows if r.get("over") not in (None, "") or r.get("under") not in (None, "")
        )
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
        return [e for e in events if date_from in _event_date_keys(e)]
    out: List[dict] = []
    for e in events:
        dates = _event_date_keys(e)
        if not dates:
            continue
        if any(
            (not date_from or d >= date_from) and (not date_to or d <= date_to)
            for d in dates
        ):
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
    Action Network (no key) first, then Odds-API.io if needed.
    """
    start = (date_from or "")[:10]
    end = (date_to or date_from or "")[:10]
    cache_key = f"nfl|{start}|{end}|{bookmakers}|v9"
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

    io_key = (api_key or "").strip()

    try:
        rows: List[dict] = []
        first_multi_raw: Any = None
        rows.extend(fetch_actionnetwork_nfl(start, end, out))
        skip_paid = _has_fd_dk_priced(rows)
        out["meta"]["skippedPaidOdds"] = skip_paid

        events: List[dict] = []
        seen_ids: set = set()
        if not skip_paid and io_key:
            attempts = (
                {"sport": "nfl", "from": start, "to": end},
                {"sport": "american-football", "league": "nfl-regular-season", "from": start, "to": end},
                {"sport": "american-football", "from": start, "to": end},
                {"sport": "nfl"},
                {"sport": "american-football", "league": "nfl-regular-season"},
            )
            for attempt in attempts:
                q_params: Dict[str, str] = {"sport": attempt["sport"], "apiKey": io_key}
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
                    else _events_in_range(raw_all, start, end) or raw_all
                )
                nfl_events = [e for e in on_range if _is_nfl_event(e)]
                added = 0
                for e in nfl_events:
                    eid = _eid_key(e.get("id"))
                    if not eid or eid in seen_ids:
                        continue
                    seen_ids.add(eid)
                    events.append(e)
                    added += 1
                if added:
                    out["meta"]["eventsSport"] = attempt["sport"]
                    if attempt.get("league"):
                        out["meta"]["eventsLeague"] = attempt["league"]
                    out["meta"].setdefault("eventsQueries", []).append(
                        ("ranged" if attempt.get("from") else "next_14d") + f"+{added}"
                    )
                if len(events) >= 8:
                    break
            if events:
                events = _events_in_range(events, start, end) or events
            out["meta"]["eventCount"] = len(events)

            if events:
                event_teams: Dict[str, Tuple[str, str]] = {}
                for e in events:
                    eid = e.get("id")
                    if eid is None:
                        continue
                    event_teams[_eid_key(eid)] = (_team_str(e.get("home")), _team_str(e.get("away")))

                event_ids = [e["id"] for e in events if e.get("id") is not None]
                io_rows, first_multi_raw = _odds_multi_rows(
                    io_key,
                    event_ids,
                    bookmakers,
                    event_teams,
                    out,
                    sport="nfl",
                    markets=None,
                    debug_structure=debug_structure,
                )
                errs = out["meta"].get("multiErrors") or []
                quota_hit = any("http_429" in str(x) or "http_403" in str(x) for x in errs)
                if not io_rows and event_ids and not quota_hit:
                    extra, first2 = _odds_multi_rows(
                        io_key,
                        event_ids,
                        "",
                        event_teams,
                        out,
                        sport="nfl",
                        markets=None,
                        debug_structure=debug_structure,
                    )
                    if extra:
                        io_rows = extra
                        if first_multi_raw is None:
                            first_multi_raw = first2
                        out["meta"]["multiFallback"] = "all_bookmakers"
                rows.extend(io_rows or [])
        elif skip_paid:
            out["meta"]["eventCount"] = 0
        else:
            out["meta"]["note"] = "no_nfl_events_for_range"

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

