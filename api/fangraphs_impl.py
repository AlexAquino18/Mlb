"""
FanGraphs + Statcast (pybaseball) — MUST live under api/ for Vercel.

Primary: FanGraphs leaderboards via pybaseball.pitching_stats (HTML scrape).
  - Patches requests with browser-like headers + timeout (many 403/empty responses without this).

Fallback: Statcast pitch-arsenal leaderboard (CSV) — same pybaseball source as matchup_impl.
  - Uses player_id (MLBAM); no FanGraphs id required.
  - Supplies K%, SwStr% proxy from whiff%; CSW% unavailable (model uses whiff-only process path).
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Full-season FG tables are large — cache per serverless instance to avoid re-downloading per pitcher.
_FG_CACHE: Dict[Tuple[int, int], pd.DataFrame] = {}
_SC_ARSENAL_CACHE: Dict[int, pd.DataFrame] = {}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fangraphs.com/leaders-legacy.aspx",
    "Cache-Control": "no-cache",
}


@contextmanager
def _browser_like_requests():
    """FanGraphs often rejects bare ``requests`` from cloud IPs; browser headers fix most failures."""
    import requests

    orig_get = requests.get
    orig_session_request = requests.Session.request

    def wrap_get(url, **kwargs):
        h = dict(_BROWSER_HEADERS)
        if kwargs.get("headers"):
            h.update(kwargs["headers"])
        kwargs["headers"] = h
        kwargs.setdefault("timeout", 55)
        return orig_get(url, **kwargs)

    def wrap_request(self, method, url, **kwargs):
        h = dict(_BROWSER_HEADERS)
        if kwargs.get("headers"):
            h.update(kwargs["headers"])
        kwargs["headers"] = h
        kwargs.setdefault("timeout", 55)
        return orig_session_request(self, method, url, **kwargs)

    requests.get = wrap_get  # type: ignore[assignment]
    requests.Session.request = wrap_request  # type: ignore[assignment]
    try:
        yield
    finally:
        requests.get = orig_get  # type: ignore[assignment]
        requests.Session.request = orig_session_request  # type: ignore[assignment]


def _num_from_row(r: pd.Series, *names: str) -> Optional[float]:
    for col in names:
        if col not in r.index:
            continue
        v = r[col]
        if pd.isna(v):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _seasons_to_try(season: int) -> List[int]:
    out = []
    for y in (season, season - 1, season - 2, season - 3):
        if y >= 2000 and y not in out:
            out.append(y)
    return out


def _row_for_fg_id(df: pd.DataFrame, fg_id: int) -> Optional[pd.Series]:
    if df is None or df.empty or "IDfg" not in df.columns:
        return None
    try:
        ids = pd.to_numeric(df["IDfg"], errors="coerce")
        m = df[ids == float(fg_id)]
        if m.empty:
            m = df[df["IDfg"] == fg_id]
        if m.empty:
            return None
        return m.iloc[0]
    except Exception:
        return None


def _cached_pitching_stats(year: int, qual: int) -> pd.DataFrame:
    key = (year, qual)
    if key in _FG_CACHE:
        return _FG_CACHE[key]

    from pybaseball import pitching_stats

    last_err: Optional[Exception] = None
    with _browser_like_requests():
        for attempt in range(3):
            try:
                df = pitching_stats(year, year, qual=qual)
                if df is None:
                    df = pd.DataFrame()
                _FG_CACHE[key] = df
                return df
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(0.35 * (2**attempt))

    if last_err:
        raise last_err
    return pd.DataFrame()


def _pitcher_name_from_arsenal_row(r: pd.Series) -> str:
    for col in ("name", "player_name", "last_name, first_name"):
        if col in r.index and pd.notna(r[col]):
            return str(r[col]).strip()
    return ""


def _statcast_arsenal_aggregate(mlbam: int, year: int) -> Optional[Dict[str, Any]]:
    """Weighted K% and whiff% from Savant pitch-arsenal-stats (pybaseball)."""
    try:
        from pybaseball import statcast_pitcher_arsenal_stats
    except ImportError:
        return None

    if year not in _SC_ARSENAL_CACHE:
        try:
            _SC_ARSENAL_CACHE[year] = statcast_pitcher_arsenal_stats(year, minPA=0)
        except Exception:
            _SC_ARSENAL_CACHE[year] = pd.DataFrame()

    df = _SC_ARSENAL_CACHE[year]
    if df is None or df.empty:
        return None

    try:
        sid = pd.to_numeric(df["player_id"], errors="coerce")
        sub = df[sid == int(mlbam)]
    except Exception:
        return None

    if sub.empty:
        return None

    # PA- and pitch-weighted aggregates (per-pitch-type rates in Savant table)
    try:
        pa = pd.to_numeric(sub["pa"], errors="coerce").fillna(0)
        pitches = pd.to_numeric(sub["pitches"], errors="coerce").fillna(0)
        k_pct_col = pd.to_numeric(sub["k_percent"], errors="coerce")
        whiff_col = pd.to_numeric(sub["whiff_percent"], errors="coerce")
    except Exception:
        return None

    total_pa = float(pa.sum())
    total_pitches = float(pitches.sum())
    if total_pa < 1 and total_pitches < 1:
        return None

    k_pct = None
    if total_pa >= 1:
        k_pct = float((k_pct_col.fillna(0) * pa).sum() / total_pa)

    whiff_pct = None
    if total_pitches >= 1:
        whiff_pct = float((whiff_col.fillna(0) * pitches).sum() / total_pitches)

    if k_pct is None and whiff_pct is None:
        return None

    name = _pitcher_name_from_arsenal_row(sub.iloc[0])

    return {
        "kPct": k_pct,
        "whiffPct": whiff_pct,
        "name": name,
    }


def get_pitcher_advanced(mlbam: int, season: int) -> Dict[str, Any]:
    try:
        from pybaseball import playerid_reverse_lookup
    except ImportError as e:
        return {"ok": False, "error": "pybaseball_import", "detail": str(e)}

    fg_id: Optional[int] = None
    try:
        rev = playerid_reverse_lookup([int(mlbam)], "mlbam")
        if rev is not None and not rev.empty:
            fg_cell = rev.iloc[0].get("key_fangraphs")
            if fg_cell is not None and not (isinstance(fg_cell, (int, float)) and int(fg_cell) < 0):
                fg_id = int(fg_cell)
    except Exception:
        pass  # Savant fallback only needs MLBAM

    # --- 1) FanGraphs (preferred): full-leaderboard scrape with browser-like requests + cache
    if fg_id is not None:
        for yr in _seasons_to_try(season):
            for qual in (1, 0):
                try:
                    df = _cached_pitching_stats(yr, qual)
                except Exception:
                    df = None
                if df is None or df.empty:
                    continue
                r = _row_for_fg_id(df, fg_id)
                if r is None:
                    continue
                sw = _num_from_row(r, "SwStr%")
                csw = _num_from_row(r, "CSW%")
                k_pct = _num_from_row(r, "K%")
                bb_pct = _num_from_row(r, "BB%")
                name = str(r.get("Name", "") or "")
                return {
                    "ok": True,
                    "season": yr,
                    "fgId": fg_id,
                    "name": name,
                    "swStr": sw,
                    "csw": csw,
                    "kPct": k_pct,
                    "bbPct": bb_pct,
                    "source": "fangraphs",
                }

    # --- 2) Statcast arsenal (Savant CSV via pybaseball) — works with MLBAM only
    for yr in _seasons_to_try(season):
        agg = _statcast_arsenal_aggregate(mlbam, yr)
        if not agg:
            continue
        k_pct = agg.get("kPct")
        whiff = agg.get("whiffPct")
        if k_pct is None and whiff is None:
            continue
        return {
            "ok": True,
            "season": yr,
            "fgId": fg_id,
            "name": agg.get("name") or "",
            # Map Savant whiff% → SwStr slot (both ~swing-miss rate scale; CSW not in this feed)
            "swStr": whiff,
            "csw": None,
            "kPct": k_pct,
            "bbPct": None,
            "source": "statcast",
        }

    err = "no_pitching_row"
    if fg_id is None:
        err = "no_fangraphs_id_and_no_statcast"
    return {"ok": False, "error": err, "fgId": fg_id}


def _init_pybaseball_disk_cache() -> None:
    """README: pybaseball cache is off by default; enable + /tmp on serverless."""
    if os.environ.get("_PYBASEBALL_DISK_CACHE"):
        return
    base = "/tmp/.pybaseball/cache" if os.name != "nt" else os.path.join(os.environ.get("TEMP", "."), ".pybaseball", "cache")
    os.environ.setdefault("PYBASEBALL_CACHE", base)
    try:
        from pybaseball import cache as pbc

        pbc.enable()
    except Exception:
        pass
    os.environ["_PYBASEBALL_DISK_CACHE"] = "1"


_init_pybaseball_disk_cache()
