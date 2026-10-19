"""
NBA projection model from public ESPN APIs.

Pulls today's slate, current rosters (so traded players sit on the right team),
injury status, last-season + current game logs, head-to-head vs tonight's
opponent, and how similar-position players have fared against that defense.
"""
from __future__ import annotations

import json
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

_CACHE: Dict[str, tuple] = {}
CACHE_TTL_SEC = 18 * 60
LOG_CACHE: Dict[str, tuple] = {}
LOG_TTL_SEC = 6 * 60 * 60

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_WEB = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"

# ESPN gamelog column order
_LOG_NAMES = [
    "minutes",
    "fg",
    "fgPct",
    "three",
    "threePct",
    "ft",
    "ftPct",
    "reb",
    "ast",
    "blk",
    "stl",
    "pf",
    "tov",
    "pts",
]

STAT_KEYS = ("pts", "reb", "ast", "threes", "stl", "blk", "tov", "min", "pra", "pr", "pa", "ra")

# Map ESPN position abbreviations onto the five prop roles.
# Roster dumps are often just G / F / C — split those with height.
_POS_MAP = {
    "PG": "PG",
    "SG": "SG",
    "SF": "SF",
    "PF": "PF",
    "C": "C",
    "G": "SG",
    "F": "SF",
    "GF": "SF",
    "G-F": "SF",
    "FG": "SF",
    "F-G": "SF",
    "FC": "PF",
    "F-C": "PF",
    "CF": "C",
    "C-F": "C",
}
_POS_FAM = {"PG": "G", "SG": "G", "SF": "F", "PF": "F", "C": "C"}
_NAME_SUFFIX = {"jr", "sr", "ii", "iii", "iv"}


def _get_json(url: str, timeout: int = 16) -> Any:
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"}, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _safe_json(url: str, timeout: int = 16) -> Tuple[Any, Optional[str]]:
    try:
        return _get_json(url, timeout=timeout), None
    except Exception as e:
        return None, str(e)[:180]


def _norm_name(s: str) -> str:
    """Match the NBA page: strip accents, letters only, sort tokens."""
    raw = unicodedata.normalize("NFD", s or "")
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    letters = []
    for ch in raw.lower():
        if "a" <= ch <= "z" or ch == " ":
            letters.append(ch)
    parts = "".join(letters).split()
    parts.sort()
    return " ".join(parts)


def _name_keys(name: str) -> List[str]:
    nk = _norm_name(name)
    keys: List[str] = []
    if nk:
        keys.append(nk)
    parts = [p for p in nk.split() if p not in _NAME_SUFFIX]
    alt = " ".join(sorted(parts))
    if alt and alt not in keys:
        keys.append(alt)
    return keys


def _num(v: Any) -> Optional[float]:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def _made(v: Any) -> Optional[float]:
    s = str(v or "")
    if "-" in s:
        return _num(s.split("-", 1)[0])
    return _num(v)


def _ymd_now() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _espn_date(date_key: str) -> str:
    return date_key.replace("-", "")


def _pos_group(abbr: str, height: Optional[float] = None) -> str:
    a = (abbr or "").upper().strip().replace(" ", "")
    h = float(height or 0)
    if a in ("C", "CF", "C-F", "C-F"):
        return "C"
    if a in ("PF", "FC", "F-C"):
        return "PF"
    if a in ("SF", "GF", "G-F", "FG", "F-G"):
        return "SF"
    if a == "PG":
        return "PG"
    if a == "SG":
        return "SG"
    if a in ("F", "FORWARD"):
        if h >= 83:
            return "C"
        return "PF" if h >= 80 else "SF"
    if a in ("G", "GUARD"):
        return "SG" if h >= 76 else "PG"
    return _POS_MAP.get(a, "SF")


def _row_from_stats(names: List[str], stats: List[Any], extra: Optional[dict] = None) -> dict:
    by = {}
    for i, name in enumerate(names or _LOG_NAMES):
        if i < len(stats):
            by[name] = stats[i]
    min_n = _num(by.get("minutes")) or 0.0
    pts = _num(by.get("points") or by.get("pts")) or 0.0
    reb = _num(by.get("totalRebounds") or by.get("reb")) or 0.0
    ast = _num(by.get("assists") or by.get("ast")) or 0.0
    threes = _made(by.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted") or by.get("three")) or 0.0
    stl = _num(by.get("steals") or by.get("stl")) or 0.0
    blk = _num(by.get("blocks") or by.get("blk")) or 0.0
    tov = _num(by.get("turnovers") or by.get("tov")) or 0.0
    row = {
        "min": round(min_n, 1),
        "pts": round(pts, 1),
        "reb": round(reb, 1),
        "ast": round(ast, 1),
        "threes": round(threes, 1),
        "stl": round(stl, 1),
        "blk": round(blk, 1),
        "tov": round(tov, 1),
        "pra": round(pts + reb + ast, 1),
        "pr": round(pts + reb, 1),
        "pa": round(pts + ast, 1),
        "ra": round(reb + ast, 1),
    }
    if extra:
        row.update(extra)
    return row


def _avg_rows(rows: List[dict], n: Optional[int] = None) -> Dict[str, float]:
    use = rows[:n] if n else rows
    if not use:
        return {}
    out: Dict[str, float] = {}
    for k in STAT_KEYS:
        vals = [_num(r.get(k)) for r in use]
        vals = [v for v in vals if v is not None]
        if vals:
            out[k] = round(sum(vals) / len(vals), 2)
    out["n"] = float(len(use))
    return out


def _parse_scoreboard(raw: dict) -> List[dict]:
    games = []
    for ev in raw.get("events") or []:
        if not isinstance(ev, dict):
            continue
        comp = (ev.get("competitions") or [None])[0] or {}
        comps = comp.get("competitors") or []
        home = away = None
        for c in comps:
            if not isinstance(c, dict):
                continue
            team = c.get("team") if isinstance(c.get("team"), dict) else {}
            recs = c.get("records") or []
            rec = ""
            if recs and isinstance(recs[0], dict):
                rec = str(recs[0].get("summary") or "")
            info = {
                "id": str(team.get("id") or ""),
                "abbr": str(team.get("abbreviation") or "").upper(),
                "name": str(team.get("displayName") or ""),
                "nick": str(team.get("shortDisplayName") or team.get("name") or ""),
                "logo": ((team.get("logos") or [{}])[0] or {}).get("href") or "",
                "score": str(c.get("score") or ""),
                "record": rec,
                "homeAway": c.get("homeAway"),
            }
            if c.get("homeAway") == "home":
                home = info
            else:
                away = info
        if not home or not away:
            continue
        status = comp.get("status") if isinstance(comp.get("status"), dict) else {}
        stype = status.get("type") if isinstance(status.get("type"), dict) else {}
        odds = ""
        ou = None
        spread = None
        for o in comp.get("odds") or []:
            if not isinstance(o, dict):
                continue
            odds = str(o.get("details") or odds)
            ou = _num(o.get("overUnder")) if o.get("overUnder") is not None else ou
            sp = o.get("spread")
            if sp is not None:
                spread = _num(sp)
            break
        venue = comp.get("venue") if isinstance(comp.get("venue"), dict) else {}
        games.append(
            {
                "id": str(ev.get("id") or ""),
                "date": ev.get("date") or "",
                "name": ev.get("name") or "",
                "home": home,
                "away": away,
                "status": str(stype.get("state") or ""),
                "statusText": str(stype.get("shortDetail") or stype.get("description") or ""),
                "completed": bool(stype.get("completed")),
                "period": status.get("period"),
                "clock": (status.get("displayClock") or ""),
                "venue": str(venue.get("fullName") or ""),
                "oddsDetails": odds,
                "overUnder": ou,
                "spread": spread,
            }
        )
    return games


def _fetch_scoreboard(date_key: str, fallback_live: bool = True) -> Tuple[List[dict], dict, Optional[str]]:
    meta: dict = {}
    url = f"{ESPN}/scoreboard?dates={_espn_date(date_key)}"
    raw, err = _safe_json(url, 18)
    if err or not isinstance(raw, dict):
        return [], meta, err or "scoreboard_failed"
    season = raw.get("season") if isinstance(raw.get("season"), dict) else {}
    leagues = raw.get("leagues") or []
    lg = leagues[0] if leagues and isinstance(leagues[0], dict) else {}
    lg_season = lg.get("season") if isinstance(lg.get("season"), dict) else {}
    meta["seasonYear"] = int(season.get("year") or lg_season.get("year") or 2027)
    meta["seasonType"] = int(season.get("type") or 2)
    meta["seasonLabel"] = str(lg_season.get("displayName") or "")
    games = _parse_scoreboard(raw)
    if not games and fallback_live:
        raw2, err2 = _safe_json(f"{ESPN}/scoreboard", 18)
        if isinstance(raw2, dict):
            games = _parse_scoreboard(raw2)
            meta["usedLiveBoard"] = True
            if err2:
                meta["liveBoardError"] = err2
    return games, meta, None


def _fetch_injuries() -> Dict[str, dict]:
    raw, err = _safe_json(f"{ESPN}/injuries", 16)
    out: Dict[str, dict] = {}
    if err or not isinstance(raw, dict):
        return out
    for block in raw.get("injuries") or []:
        if not isinstance(block, dict):
            continue
        for row in block.get("injuries") or []:
            if not isinstance(row, dict):
                continue
            ath = row.get("athlete") if isinstance(row.get("athlete"), dict) else {}
            name = ath.get("displayName") or ath.get("fullName") or ""
            name = str(name)
            if not _norm_name(name):
                continue
            st = row.get("status") if isinstance(row.get("status"), dict) else {}
            rec = {
                "status": str(row.get("status") or st.get("type") or st.get("abbreviation") or ""),
                "detail": str(row.get("longComment") or row.get("shortComment") or row.get("type") or ""),
                "id": str(ath.get("id") or ""),
            }
            for nk in _name_keys(name):
                out[nk] = rec
    # Alternate shape: sports → leagues → teams → injuries
    if not out:
        for sp in raw.get("sports") or []:
            for lg in (sp or {}).get("leagues") or []:
                for team in (lg or {}).get("teams") or []:
                    for row in (team or {}).get("injuries") or []:
                        ath = row.get("athlete") if isinstance(row.get("athlete"), dict) else {}
                        nm = str(ath.get("displayName") or "")
                        if _norm_name(nm):
                            rec = {
                                "status": str(row.get("status") or ""),
                                "detail": str(row.get("longComment") or row.get("description") or ""),
                                "id": str(ath.get("id") or ""),
                            }
                            for nk in _name_keys(nm):
                                out[nk] = rec
    return out


def _fetch_roster(team_id: str) -> List[dict]:
    raw, err = _safe_json(f"{ESPN}/teams/{team_id}/roster", 16)
    players: List[dict] = []
    if err or not isinstance(raw, dict):
        return players
    team = raw.get("team") if isinstance(raw.get("team"), dict) else {}
    abbr = str(team.get("abbreviation") or "").upper()
    for ath in raw.get("athletes") or []:
        if not isinstance(ath, dict):
            continue
        pos = ath.get("position") if isinstance(ath.get("position"), dict) else {}
        st = ath.get("status") if isinstance(ath.get("status"), dict) else {}
        exp = ath.get("experience") if isinstance(ath.get("experience"), dict) else {}
        name = str(ath.get("displayName") or ath.get("fullName") or "")
        if not name:
            continue
        years = int(exp.get("years") or 0)
        height = _num(ath.get("height"))
        inj0 = (ath.get("injuries") or [None])[0] or {}
        roster_inj = str(inj0.get("status") or "") if isinstance(inj0, dict) else ""
        players.append(
            {
                "id": str(ath.get("id") or ""),
                "name": name,
                "nk": _norm_name(name),
                "team": abbr,
                "teamId": str(team.get("id") or team_id),
                "pos": _pos_group(str(pos.get("abbreviation") or pos.get("name") or ""), height),
                "posRaw": str(pos.get("abbreviation") or ""),
                "jersey": str(ath.get("jersey") or ""),
                "years": years,
                "height": height,
                "rosterInj": roster_inj,
                "active": str(st.get("type") or "active").lower() != "inactive",
            }
        )
    players.sort(key=lambda p: (-int(p.get("years") or 0), p.get("name") or ""))
    return players


def _fetch_gamelog(athlete_id: str, season: int) -> List[dict]:
    ck = f"{athlete_id}|{season}"
    now = time.time()
    if ck in LOG_CACHE:
        ts, data = LOG_CACHE[ck]
        if now - ts < LOG_TTL_SEC:
            return data
    url = f"{ESPN_WEB}/athletes/{athlete_id}/gamelog?season={season}"
    raw, err = _safe_json(url, 14)
    rows: List[dict] = []
    if err or not isinstance(raw, dict):
        LOG_CACHE[ck] = (now, rows)
        return rows
    names = raw.get("names") or _LOG_NAMES
    events_meta = raw.get("events") if isinstance(raw.get("events"), dict) else {}
    for stype in raw.get("seasonTypes") or []:
        if not isinstance(stype, dict):
            continue
        label = str(stype.get("displayName") or "")
        if "preseason" in label.lower():
            continue
        for cat in stype.get("categories") or []:
            if not isinstance(cat, dict):
                continue
            for ev in cat.get("events") or []:
                if not isinstance(ev, dict):
                    continue
                eid = str(ev.get("eventId") or "")
                meta = events_meta.get(eid) if isinstance(events_meta.get(eid), dict) else {}
                opp = meta.get("opponent") if isinstance(meta.get("opponent"), dict) else {}
                team = meta.get("team") if isinstance(meta.get("team"), dict) else {}
                row = _row_from_stats(
                    names if isinstance(names, list) else _LOG_NAMES,
                    ev.get("stats") or [],
                    {
                        "eventId": eid,
                        "date": str(meta.get("gameDate") or "")[:10],
                        "opp": str(opp.get("abbreviation") or "").upper(),
                        "team": str(team.get("abbreviation") or "").upper(),
                        "result": str(meta.get("gameResult") or ""),
                        "home": str(meta.get("atVs") or "") != "@",
                        "split": label,
                    },
                )
                rows.append(row)
    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    LOG_CACHE[ck] = (now, rows)
    return rows


def _blend(season: dict, recent: dict, last5: dict, h2h: dict, key: str) -> Optional[float]:
    s = _num(season.get(key))
    r = _num(recent.get(key))
    f = _num(last5.get(key))
    h = _num(h2h.get(key))
    hn = int(h2h.get("n") or 0)
    fn = int(last5.get("n") or 0)
    if r is None and s is None and h is None and f is None:
        return None
    w_h = w_f = 0.0
    if hn >= 3 and h is not None:
        w_h = 0.18
    elif hn >= 1 and h is not None:
        w_h = 0.10
    if fn >= 3 and f is not None:
        w_f = 0.22
    w_r, w_s = 0.42, 0.28
    if w_h == 0:
        w_r += 0.08
        w_s += 0.04
    if w_f == 0:
        w_r += 0.12
    r = r if r is not None else (s or f or 0.0)
    s = s if s is not None else r
    f = f if f is not None else r
    h = h if h is not None else 0.0
    denom = w_h + w_f + w_r + w_s
    return (w_h * h + w_f * f + w_r * r + w_s * s) / denom


def _dd_rate(rows: List[dict], n_cats: int = 2) -> Optional[float]:
    use = [r for r in rows if (r.get("min") or 0) >= 8][:40]
    if not use:
        return None
    hits = 0
    for r in use:
        cats = sum(1 for k in ("pts", "reb", "ast") if (r.get(k) or 0) >= 10)
        if cats >= n_cats:
            hits += 1
    return round(hits / len(use), 3)


def _def_bucket(vs_pos: dict, lg_pos: dict, opp: str, pos: str) -> Tuple[dict, dict, str]:
    by_opp = vs_pos.get(opp) or {}
    d = by_opp.get(pos) or {}
    lg = lg_pos.get(pos) or {}
    label = pos
    if int(d.get("n") or 0) < 4:
        fam = _POS_FAM.get(pos, pos)
        d2 = by_opp.get(fam) or {}
        if int(d2.get("n") or 0) > int(d.get("n") or 0):
            d, lg, label = d2, (lg_pos.get(fam) or lg), fam
    return d, lg, label


def _project_player(
    pl: dict,
    opp_abbr: str,
    vs_pos: dict,
    lg_pos: dict,
    *,
    b2b: bool = False,
    over_under: Optional[float] = None,
    is_home: bool = False,
) -> dict:
    logs: List[dict] = pl.get("logs") or []
    qualified = [r for r in logs if (r.get("min") or 0) >= 8]
    last5 = _avg_rows(qualified[:5])
    recent = _avg_rows(qualified[:10])
    season = _avg_rows(qualified[:50])
    h2h_rows = [r for r in qualified if r.get("opp") == opp_abbr]
    h2h = _avg_rows(h2h_rows[:8])
    pos = pl.get("pos") or "SF"
    def_vs, lg, vs_label = _def_bucket(vs_pos, lg_pos, opp_abbr, pos)
    role_min = _num(last5.get("min")) or _num(recent.get("min")) or _num(season.get("min")) or 22.0
    status = str(pl.get("injStatus") or pl.get("rosterInj") or "").lower()
    if any(x in status for x in ("out", "inactive", "ir", "susp")):
        role_min *= 0.05
    elif "doubt" in status:
        role_min *= 0.55
    elif "question" in status:
        role_min *= 0.82
    if b2b:
        role_min *= 0.93
    min_factor = 1.0
    base_min = _num(recent.get("min")) or role_min
    if base_min > 1:
        min_factor = max(0.70, min(1.20, role_min / base_min))
    pace = 1.0
    if over_under and over_under > 0:
        pace = max(0.92, min(1.08, 0.97 + 0.03 * (over_under / 224.0)))
    loc = 1.02 if is_home else 0.99
    proj: Dict[str, Any] = {"min": round(role_min, 1)}
    notes = []
    if h2h.get("n"):
        notes.append(f"H2H {int(h2h['n'])}g vs {opp_abbr}")
    if b2b:
        notes.append("back-to-back")
    for key in ("pts", "reb", "ast", "threes", "stl", "blk", "tov"):
        base = _blend(season, recent, last5, h2h, key)
        if base is None:
            continue
        allowed = _num(def_vs.get(key))
        lg_avg = _num(lg.get(key))
        tilt = 1.0
        if allowed is not None and lg_avg and lg_avg > 0.4:
            tilt = 1.0 + 0.38 * ((allowed / lg_avg) - 1.0)
            tilt = max(0.82, min(1.22, tilt))
        loc_k = loc if key in ("pts", "threes", "ast") else 1.0
        val = base * min_factor * tilt * pace * loc_k
        proj[key] = round(val, 1)
    if "pts" in proj:
        if "reb" in proj:
            proj["pr"] = round(proj["pts"] + proj["reb"], 1)
        if "ast" in proj:
            proj["pa"] = round(proj["pts"] + proj["ast"], 1)
        if "reb" in proj and "ast" in proj:
            proj["pra"] = round(proj["pts"] + proj["reb"] + proj["ast"], 1)
            proj["ra"] = round(proj["reb"] + proj["ast"], 1)
    dd = _dd_rate(qualified, 2)
    td = _dd_rate(qualified, 3)
    if dd is not None:
        proj["ddbl"] = round(dd * min_factor, 3)
    if td is not None:
        proj["tdbl"] = round(td * min_factor, 3)
    if def_vs:
        hot = []
        for k, label in (("pts", "PTS"), ("reb", "REB"), ("ast", "AST"), ("threes", "3PM")):
            a = _num(def_vs.get(k))
            l = _num(lg.get(k))
            if a is not None and l and l > 0:
                pct = (a / l - 1) * 100
                if abs(pct) >= 6:
                    hot.append(f"{opp_abbr} {pct:+.0f}% {label} to {vs_label}s")
        if hot:
            notes.extend(hot[:2])
    pl["recent"] = recent
    pl["seasonAvg"] = season
    pl["h2h"] = {
        "vs": opp_abbr,
        "n": int(h2h.get("n") or 0),
        "pts": h2h.get("pts"),
        "reb": h2h.get("reb"),
        "ast": h2h.get("ast"),
        "threes": h2h.get("threes"),
        "min": h2h.get("min"),
        "pra": h2h.get("pra"),
    }
    pl["proj"] = proj
    pl["vsDef"] = {
        "pos": vs_label,
        "n": int(def_vs.get("n") or 0),
        "pts": def_vs.get("pts"),
        "reb": def_vs.get("reb"),
        "ast": def_vs.get("ast"),
    }
    pl["role"] = {
        "minutes": round(role_min, 1),
        "pos": pos,
        "b2b": b2b,
        "note": " · ".join(notes[:3]),
    }
    return pl


def get_nba_model(date_key: str = "") -> Dict[str, Any]:
    day = (date_key or _ymd_now())[:10]
    if len(day) != 10:
        day = _ymd_now()
    cache_key = f"nba|{day}|v3"
    now = time.time()
    if cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if now - ts < CACHE_TTL_SEC and data.get("ok"):
            return data

    out: Dict[str, Any] = {
        "ok": False,
        "date": day,
        "players": {},
        "vsPos": {},
        "games": [],
        "source": "espn-nba",
    }
    games, meta, err = _fetch_scoreboard(day)
    out.update({k: v for k, v in meta.items()})
    out["games"] = games
    if err and not games:
        out["error"] = err
        return out

    injuries = {}
    try:
        injuries = _fetch_injuries()
    except Exception as e:
        out["injError"] = str(e)[:160]

    team_ids: List[Tuple[str, str, str]] = []
    seen_tid = set()
    for g in games:
        for side in ("home", "away"):
            t = g.get(side) or {}
            tid = str(t.get("id") or "")
            if tid and tid not in seen_tid:
                seen_tid.add(tid)
                team_ids.append((tid, str(t.get("abbr") or ""), side))

    rosters: Dict[str, List[dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_roster, tid): tid for tid, _, _ in team_ids}
        for fut in as_completed(futs):
            tid = futs[fut]
            try:
                rosters[tid] = fut.result()
            except Exception:
                rosters[tid] = []

    # Yesterday's board — back-to-backs crush minutes and counting stats.
    yday = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    ygames, _, _ = _fetch_scoreboard(yday, fallback_live=False)
    played_yest: set = set()
    for g in ygames or []:
        for side in ("home", "away"):
            ab = str(((g.get(side) or {}).get("abbr") or "")).upper()
            if ab:
                played_yest.add(ab)

    game_of: Dict[str, dict] = {}
    opp_of: Dict[str, str] = {}
    home_ids: set = set()
    for g in games:
        hid = str((g.get("home") or {}).get("id") or "")
        aid = str((g.get("away") or {}).get("id") or "")
        ha = str((g.get("home") or {}).get("abbr") or "")
        aa = str((g.get("away") or {}).get("abbr") or "")
        if hid:
            opp_of[hid] = aa
            game_of[hid] = g
            home_ids.add(hid)
        if aid:
            opp_of[aid] = ha
            game_of[aid] = g

    n_teams = max(1, len(team_ids))
    if n_teams <= 4:
        per_team, max_proj = 12, 44
    elif n_teams <= 8:
        per_team, max_proj = 9, 48
    else:
        per_team, max_proj = 7, 56

    pool: List[dict] = []
    for tid, _abbr, _side in team_ids:
        team_players = rosters.get(tid) or []
        ranked = []
        for p in team_players:
            inj = injuries.get(p["nk"]) or {}
            p["injStatus"] = inj.get("status") or p.get("rosterInj") or ""
            p["injDetail"] = inj.get("detail") or ""
            st = str(p["injStatus"]).lower()
            if any(x in st for x in ("out", "inactive", "ir", "susp")):
                continue
            if not p.get("active", True):
                continue
            ranked.append(p)
        ranked.sort(key=lambda p: (-int(p.get("years") or 0), p.get("name") or ""))
        pool.extend(ranked[:per_team])

    season_now = int(out.get("seasonYear") or 2027)
    try:
        month = int(day[5:7])
        dom = int(day[8:10])
    except Exception:
        month, dom = 10, 1
    include_current = not (month == 10 and dom < 25)

    def _load_logs(pl: dict) -> dict:
        logs: List[dict] = []
        seen_e = set()
        if not pl.get("id"):
            pl["logs"] = []
            return pl

        def add_year(yr: int) -> None:
            for row in _fetch_gamelog(str(pl["id"]), yr):
                eid = row.get("eventId")
                if eid in seen_e:
                    continue
                seen_e.add(eid)
                logs.append(row)

        add_year(season_now - 1)
        qualified = [r for r in logs if (r.get("min") or 0) >= 8]
        if len(qualified) < 40:
            add_year(season_now - 2)
        if include_current:
            add_year(season_now)
        logs.sort(key=lambda r: r.get("date") or "", reverse=True)
        pl["logs"] = logs[:82]
        return pl

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(_load_logs, pool))

    # Position-vs-team uses every fetched rotation log, including guys we later drop.
    vs_acc: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
    for pl in pool:
        pos = pl.get("pos") or "SF"
        fam = _POS_FAM.get(pos, pos)
        for row in pl.get("logs") or []:
            if (row.get("min") or 0) < 10:
                continue
            opp = row.get("opp") or ""
            if not opp:
                continue
            for bucket_pos in (pos, fam):
                bucket = vs_acc.setdefault(opp, {}).setdefault(bucket_pos, {})
                for k in ("pts", "reb", "ast", "threes", "stl", "blk", "tov"):
                    bucket.setdefault(k, []).append(float(row.get(k) or 0))

    vs_pos: Dict[str, dict] = {}
    lg_pos: Dict[str, Dict[str, List[float]]] = {}
    for opp, by_pos in vs_acc.items():
        vs_pos[opp] = {}
        for pos, stats in by_pos.items():
            rec = {"n": len(next(iter(stats.values()), []))}
            for k, vals in stats.items():
                if vals:
                    rec[k] = round(sum(vals) / len(vals), 2)
                    lg_pos.setdefault(pos, {}).setdefault(k, []).extend(vals)
            vs_pos[opp][pos] = rec
    lg_avg: Dict[str, dict] = {}
    for pos, stats in lg_pos.items():
        lg_avg[pos] = {k: round(sum(v) / len(v), 2) for k, v in stats.items() if v}

    # Keep the actual rotation — minutes last, not veteran years.
    keep_n = max(6, min(per_team, max_proj // n_teams))
    by_team: Dict[str, List[dict]] = {}
    for pl in pool:
        tid = str(pl.get("teamId") or "")
        mins = _num(((pl.get("logs") or [{}])[0]).get("min")) or 0.0
        recent_m = _avg_rows([r for r in (pl.get("logs") or []) if (r.get("min") or 0) >= 8][:12])
        pl["_rankMin"] = _num(recent_m.get("min")) or mins
        by_team.setdefault(tid, []).append(pl)
    picks: List[dict] = []
    for tid, group in by_team.items():
        group.sort(key=lambda p: (-float(p.get("_rankMin") or 0), -int(p.get("years") or 0)))
        picks.extend(group[:keep_n])
    picks = picks[:max_proj]

    players_out: Dict[str, dict] = {}
    seen_ids: set = set()
    for pl in picks:
        tid = str(pl.get("teamId") or "")
        opp = opp_of.get(tid, "")
        g = game_of.get(tid) or {}
        _project_player(
            pl,
            opp,
            vs_pos,
            lg_avg,
            b2b=str(pl.get("team") or "").upper() in played_yest,
            over_under=_num(g.get("overUnder")),
            is_home=tid in home_ids,
        )
        slim_logs = [
            {k: r.get(k) for k in ("date", "opp", "min", "pts", "reb", "ast", "threes", "stl", "blk", "pra", "result")}
            for r in (pl.get("logs") or [])[:12]
        ]
        rec = {
            "name": pl["name"],
            "team": pl["team"],
            "teamId": pl["teamId"],
            "pos": pl.get("pos"),
            "posRaw": pl.get("posRaw"),
            "espnId": pl.get("id"),
            "jersey": pl.get("jersey"),
            "injStatus": pl.get("injStatus"),
            "injDetail": pl.get("injDetail"),
            "stats": pl.get("proj") or {},
            "recent": pl.get("recent") or {},
            "seasonAvg": pl.get("seasonAvg") or {},
            "h2h": pl.get("h2h") or {},
            "vsDef": pl.get("vsDef") or {},
            "role": pl.get("role") or {},
            "logs": slim_logs,
        }
        seen_ids.add(str(pl.get("id") or pl["nk"]))
        for k in _name_keys(pl["name"]):
            players_out[k] = rec

    out["ok"] = True
    out["players"] = players_out
    out["vsPos"] = vs_pos
    out["lgPos"] = lg_avg
    out["playerCount"] = len(seen_ids)
    out["gameCount"] = len(games)
    _CACHE[cache_key] = (now, out)
    return out
