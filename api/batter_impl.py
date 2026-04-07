"""
Statcast batter advanced metrics — Savant CSV via requests (no pybaseball).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from savant_http import batter_exitvelo_barrels, batter_expected_stats

_SC_EXP: Dict[int, pd.DataFrame] = {}
_SC_BRL: Dict[int, pd.DataFrame] = {}


def _seasons_to_try(season: int) -> List[int]:
    out = []
    for y in (season, season - 1, season - 2):
        if y >= 2000 and y not in out:
            out.append(y)
    return out


def _num(r: pd.Series, *names: str) -> Optional[float]:
    for n in names:
        if n not in r.index:
            continue
        v = r[n]
        if pd.isna(v):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def get_batter_advanced(mlbam: int, season: int) -> Dict[str, Any]:
    for yr in _seasons_to_try(season):
        if yr not in _SC_EXP:
            try:
                _SC_EXP[yr] = batter_expected_stats(yr, min_pa=0)
            except Exception:
                _SC_EXP[yr] = pd.DataFrame()
        exp = _SC_EXP[yr]
        if exp is None or exp.empty:
            continue
        try:
            sid = pd.to_numeric(exp["player_id"], errors="coerce")
            row = exp[sid == int(mlbam)]
        except Exception:
            continue
        if row.empty:
            continue
        r = row.iloc[0]

        brl_pct = None
        if yr not in _SC_BRL:
            try:
                _SC_BRL[yr] = batter_exitvelo_barrels(yr, min_bbe=0)
            except Exception:
                _SC_BRL[yr] = pd.DataFrame()
        br = _SC_BRL[yr]
        if br is not None and not br.empty:
            try:
                bid = pd.to_numeric(br["player_id"], errors="coerce")
                brw = br[bid == int(mlbam)]
                if not brw.empty:
                    brl_pct = _num(brw.iloc[0], "brl_percent")
            except Exception:
                pass

        return {
            "ok": True,
            "season": yr,
            "xwoba": _num(r, "est_woba", "xwoba"),
            "woba": _num(r, "woba"),
            "xba": _num(r, "est_ba", "xba"),
            "ba": _num(r, "ba"),
            "xslg": _num(r, "est_slg", "xslg"),
            "slg": _num(r, "slg"),
            "brl_percent": brl_pct,
            "pa": _num(r, "pa"),
            "source": "statcast",
        }

    return {"ok": False, "error": "no_statcast_batter_row"}
