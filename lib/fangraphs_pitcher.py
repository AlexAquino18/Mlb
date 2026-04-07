"""
FanGraphs advanced pitching stats via pybaseball (SwStr%, CSW%, etc.).
Used by Vercel /api/fangraphs_pitcher.py and optionally server.py for local dev.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


def get_pitcher_advanced(mlbam: int, season: int) -> Dict[str, Any]:
    """
    Map MLBAM id -> FanGraphs ID, then pull season pitching row from FanGraphs leaders.
    Falls back to previous season if current year has no / insufficient row (early season).
    """
    try:
        from pybaseball import pitching_stats, playerid_reverse_lookup
    except ImportError as e:
        return {"ok": False, "error": "pybaseball_import", "detail": str(e)}

    try:
        # Chadwick table stores key_mlbam as int — string IDs fail isin() match
        rev = playerid_reverse_lookup([int(mlbam)], "mlbam")
    except Exception as e:
        return {"ok": False, "error": "reverse_lookup_failed", "detail": str(e)}

    if rev is None or rev.empty:
        return {"ok": False, "error": "no_reverse_lookup"}

    fg_cell = rev.iloc[0].get("key_fangraphs")
    if fg_cell is None or (isinstance(fg_cell, (int, float)) and int(fg_cell) < 0):
        return {"ok": False, "error": "no_fangraphs_id"}

    fg_id = int(fg_cell)

    def num_from_row(r: pd.Series, *names: str) -> Optional[float]:
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

    for yr in (season, season - 1):
        try:
            df = pitching_stats(yr, yr, qual=1)
        except Exception:
            continue
        if df is None or df.empty or "IDfg" not in df.columns:
            continue
        m = df[df["IDfg"] == fg_id]
        if m.empty:
            continue
        r = m.iloc[0]
        sw = num_from_row(r, "SwStr%")
        csw = num_from_row(r, "CSW%")
        k_pct = num_from_row(r, "K%")
        bb_pct = num_from_row(r, "BB%")
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
