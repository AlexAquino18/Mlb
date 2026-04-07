"""
Pitcher advanced rates for the SO model — Statcast only (Savant CSV via requests).
FanGraphs/pybaseball was removed from the serverless bundle to stay under Vercel 500 MB.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from savant_http import pitcher_pitch_arsenal_stats

# Cache per serverless instance
_SC_ARSENAL_CACHE: Dict[int, pd.DataFrame] = {}


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


def _pitcher_name_from_arsenal_row(r: pd.Series) -> str:
    for col in ("name", "player_name", "last_name, first_name"):
        if col in r.index and pd.notna(r[col]):
            return str(r[col]).strip()
    return ""


def _statcast_arsenal_aggregate(mlbam: int, year: int) -> Optional[Dict[str, Any]]:
    """Weighted K% and whiff% from Savant pitch-arsenal-stats CSV."""
    if year not in _SC_ARSENAL_CACHE:
        try:
            _SC_ARSENAL_CACHE[year] = pitcher_pitch_arsenal_stats(year, min_pa=0)
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
    fg_id = None  # FanGraphs id not resolved without Chadwick bundle

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
            "swStr": round(whiff, 3) if whiff is not None else None,
            "csw": None,
            "kPct": round(k_pct, 3) if k_pct is not None else None,
            "bbPct": None,
            "source": "statcast",
        }

    return {"ok": False, "error": "no_statcast_pitcher_row", "fgId": fg_id}
