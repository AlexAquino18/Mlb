"""
FanGraphs / pybaseball — MUST live under api/ so Vercel Python bundles it with the handler.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


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


def get_pitcher_advanced(mlbam: int, season: int) -> Dict[str, Any]:
    try:
        from pybaseball import pitching_stats, playerid_reverse_lookup
    except ImportError as e:
        return {"ok": False, "error": "pybaseball_import", "detail": str(e)}

    try:
        rev = playerid_reverse_lookup([int(mlbam)], "mlbam")
    except Exception as e:
        return {"ok": False, "error": "reverse_lookup_failed", "detail": str(e)}

    if rev is None or rev.empty:
        return {"ok": False, "error": "no_reverse_lookup"}

    fg_cell = rev.iloc[0].get("key_fangraphs")
    if fg_cell is None or (isinstance(fg_cell, (int, float)) and int(fg_cell) < 0):
        return {"ok": False, "error": "no_fangraphs_id"}

    fg_id = int(fg_cell)

    for yr in _seasons_to_try(season):
        df = None
        try:
            df = pitching_stats(yr, yr, qual=1)
        except Exception:
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
        }

    return {"ok": False, "error": "no_pitching_row", "fgId": fg_id}
