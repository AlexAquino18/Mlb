"""
ESPN NFL weekly projections + FPI matchup ratings.
Used by Vercel /api/nfl_stats and local server.py.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

_CACHE: Dict[str, tuple] = {}
CACHE_TTL_SEC = 20 * 60

# ESPN fantasy proTeamId → NFL abbreviation
PRO_TEAMS = {
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GB",
    10: "TEN",
    11: "IND",
    12: "KC",
    13: "LV",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NE",
    18: "NO",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SF",
    26: "SEA",
    27: "TB",
    28: "WSH",
    29: "CAR",
    30: "JAX",
    33: "BAL",
    34: "HOU",
}

# ESPN fantasy stat ids → PropPulse keys
STAT_IDS = {
    "0": "pass_att",
    "1": "completions",
    "3": "pass_yds",
    "4": "pass_td",
    "20": "ints",
    "23": "rush_att",
    "24": "rush_yds",
    "25": "rush_td",
    "42": "rec_yds",
    "43": "rec_td",
    "53": "receptions",
    "58": "targets",
}


def _get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 22) -> Any:
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _norm_name(s: str) -> str:
    raw = (s or "").lower().strip()
    out = []
    for ch in raw:
        if "a" <= ch <= "z" or ch == " ":
            out.append(ch)
    return " ".join("".join(out).split())


def _num(v: Any) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _fetch_projections(season: int, week: int) -> Dict[str, dict]:
    filt = json.dumps(
        {
            "players": {
                "filterSlotIds": {"value": [0, 2, 3, 4, 5, 6, 16, 17, 23]},
                "limit": 400,
                "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            }
        }
    )
    qs = urllib.parse.urlencode(
        {
            "view": "kona_player_info",
            "scoringPeriodId": str(week),
        }
    )
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/"
        f"segments/0/leaguedefaults/3?{qs}"
    )
    raw = _get_json(url, {"X-Fantasy-Filter": filt})
    players = raw.get("players") if isinstance(raw, dict) else []
    out: Dict[str, dict] = {}
    if not isinstance(players, list):
        return out
    for row in players:
        if not isinstance(row, dict):
            continue
        pl = row.get("player") if isinstance(row.get("player"), dict) else {}
        name = pl.get("fullName") or pl.get("lastName") or ""
        nk = _norm_name(str(name))
        if not nk:
            continue
        team_id = int(pl.get("proTeamId") or 0)
        stats_list = pl.get("stats") if isinstance(pl.get("stats"), list) else []
        chosen = None
        for st in stats_list:
            if not isinstance(st, dict):
                continue
            if st.get("statSourceId") == 1 and int(st.get("scoringPeriodId") or 0) == week:
                chosen = st
                break
        if not chosen:
            continue
        ids = chosen.get("stats") if isinstance(chosen.get("stats"), dict) else {}
        mapped: Dict[str, float] = {}
        for sid, key in STAT_IDS.items():
            n = _num(ids.get(sid))
            if n is None and sid.isdigit():
                n = _num(ids.get(int(sid)))
            if n is not None:
                mapped[key] = round(n, 3)
        if not mapped:
            continue
        if "pass_yds" in mapped or "rush_yds" in mapped:
            mapped["pass_rush_yds"] = round(
                (mapped.get("pass_yds") or 0) + (mapped.get("rush_yds") or 0), 3
            )
        if "rush_yds" in mapped or "rec_yds" in mapped:
            mapped["rush_rec_yds"] = round(
                (mapped.get("rush_yds") or 0) + (mapped.get("rec_yds") or 0), 3
            )
        if "pass_yds" in mapped or "rush_yds" in mapped or "rec_yds" in mapped:
            mapped["pass_rush_rec_yds"] = round(
                (mapped.get("pass_yds") or 0)
                + (mapped.get("rush_yds") or 0)
                + (mapped.get("rec_yds") or 0),
                3,
            )
        if "pass_td" in mapped or "rush_td" in mapped:
            mapped["pass_rush_td"] = round(
                (mapped.get("pass_td") or 0) + (mapped.get("rush_td") or 0), 3
            )
        if "rush_td" in mapped or "rec_td" in mapped:
            mapped["rush_rec_td"] = round(
                (mapped.get("rush_td") or 0) + (mapped.get("rec_td") or 0), 3
            )
            mapped["anytime_td"] = mapped["rush_rec_td"]
        pts = _num(chosen.get("appliedTotal"))
        if pts is not None:
            mapped["fantasy"] = round(pts, 3)
        out[nk] = {
            "name": str(name),
            "team": PRO_TEAMS.get(team_id, ""),
            "teamId": team_id,
            "stats": mapped,
        }
    return out


def _fetch_fpi() -> Dict[str, dict]:
    url = "https://site.web.api.espn.com/apis/fitt/v3/sports/football/nfl/powerindex?limit=32"
    raw = _get_json(url)
    teams = raw.get("teams") if isinstance(raw, dict) else []
    out: Dict[str, dict] = {}
    if not isinstance(teams, list):
        return out
    off_vals = []
    def_vals = []
    parsed = []
    for row in teams:
        if not isinstance(row, dict):
            continue
        team = row.get("team") if isinstance(row.get("team"), dict) else {}
        abbr = str(team.get("abbreviation") or "").upper()
        cats = row.get("categories") if isinstance(row.get("categories"), list) else []
        vals = []
        for cat in cats:
            if isinstance(cat, dict) and cat.get("name") == "fpi":
                vals = cat.get("values") or []
                break
        if not abbr or not isinstance(vals, list) or len(vals) < 3:
            continue
        fpi = _num(vals[0]) or 0.0
        off = _num(vals[1]) or 0.0
        de = _num(vals[2]) or 0.0
        off_vals.append(off)
        def_vals.append(de)
        parsed.append((abbr, fpi, off, de, str(team.get("displayName") or "")))
    avg_off = sum(off_vals) / len(off_vals) if off_vals else 0.0
    avg_def = sum(def_vals) / len(def_vals) if def_vals else 0.0
    for abbr, fpi, off, de, name in parsed:
        out[abbr] = {
            "name": name,
            "fpi": round(fpi, 3),
            "epaOff": round(off, 3),
            "epaDef": round(de, 3),
            "offDelta": round(off - avg_off, 3),
            "defDelta": round(de - avg_def, 3),
        }
    return out


def get_nfl_model(season: int, week: int) -> Dict[str, Any]:
    key = f"{season}|{week}|v1"
    now = time.time()
    if key in _CACHE:
        ts, data = _CACHE[key]
        if now - ts < CACHE_TTL_SEC and data.get("ok"):
            return data
    out: Dict[str, Any] = {
        "ok": False,
        "season": season,
        "week": week,
        "players": {},
        "fpi": {},
        "source": "espn-fantasy+fpi",
    }
    try:
        players = _fetch_projections(season, week)
        fpi = {}
        try:
            fpi = _fetch_fpi()
        except Exception as e:
            out["fpiError"] = str(e)[:180]
        out["ok"] = True
        out["players"] = players
        out["fpi"] = fpi
        out["playerCount"] = len(players)
        out["fpiCount"] = len(fpi)
        _CACHE[key] = (now, out)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        out["error"] = f"http_{e.code}: {body[:240]}"
    except Exception as e:
        out["error"] = str(e)
    return out
