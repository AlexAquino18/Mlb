"""
Statcast matchup via pybaseball: pitcher pitch mix × batter whiff% vs each pitch type.
Uses Savant leaderboards: statcast_pitcher_arsenal_stats, statcast_batter_pitch_arsenal.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

# Module-level cache (warm per cold start; avoids re-downloading 3k+ rows each batter)
_ARS_P: Dict[int, pd.DataFrame] = {}
_ARS_B: Dict[int, pd.DataFrame] = {}

LG_WHIFF = 24.0  # ~league avg swing-and-miss% (Statcast), used when a cell is missing


def _load_arsenals(year: int):
    """Fetch pitcher/batter pitch-type stat tables for a season (cached)."""
    try:
        from pybaseball import statcast_batter_pitch_arsenal, statcast_pitcher_arsenal_stats
    except ImportError:
        return None, None

    if year not in _ARS_P:
        try:
            _ARS_P[year] = statcast_pitcher_arsenal_stats(year, minPA=10)
        except Exception:
            _ARS_P[year] = pd.DataFrame()

    if year not in _ARS_B:
        try:
            _ARS_B[year] = statcast_batter_pitch_arsenal(year, minPA=10)
        except Exception:
            _ARS_B[year] = pd.DataFrame()

    return _ARS_P.get(year), _ARS_B.get(year)


def compute_so_matchup(pitcher_mlbam: int, batter_ids: List[int], season: int) -> Dict[str, Any]:
    """
    Expected whiff% if this lineup faces this pitcher's actual mix:
      sum_p usage_p * (lineup-weighted batter whiff% vs pitch p)
    Returns kPctAdj (percentage points to add to effective K%) — small, capped.
    """
    if not pitcher_mlbam or not batter_ids:
        return {"ok": False, "error": "missing_ids"}

    batter_ids = [int(x) for x in batter_ids[:9] if x]
    if not batter_ids:
        return {"ok": False, "error": "no_batters"}

    weights = [1.25, 1.20, 1.15, 1.05, 1.00, 0.95, 0.85, 0.80, 0.80]
    total_w = sum(weights[: len(batter_ids)])

    p_df: Optional[pd.DataFrame] = None
    b_df: Optional[pd.DataFrame] = None
    used_year = season

    for yr in (season, season - 1, season - 2):
        p_all, b_all = _load_arsenals(yr)
        if p_all is None or b_all is None or p_all.empty or b_all.empty:
            continue
        pm = p_all[p_all["player_id"] == int(pitcher_mlbam)]
        if pm.empty:
            continue
        p_df = pm
        b_df = b_all
        used_year = yr
        break

    if p_df is None or b_df is None or p_df.empty:
        return {"ok": False, "error": "no_pitcher_arsenal", "seasonTried": season}

    # Pitch mix: usage sums ~100 across pitch types for this pitcher
    mix_rows = []
    for _, prow in p_df.iterrows():
        pt = str(prow.get("pitch_type") or "").strip()
        if not pt or pt == "null":
            continue
        try:
            usage = float(prow.get("pitch_usage") or 0) / 100.0
        except (TypeError, ValueError):
            continue
        if usage <= 0:
            continue
        try:
            pw = float(prow.get("whiff_percent") or 0)
        except (TypeError, ValueError):
            pw = 0.0
        mix_rows.append(
            {
                "pitch": pt,
                "name": str(prow.get("pitch_name") or pt),
                "usagePct": round(usage * 100, 1),
                "pitcherWhiffPct": round(pw, 1),
            }
        )

    mix_rows.sort(key=lambda x: -x["usagePct"])

    # Weighted lineup: for each batter, expected whiff vs THIS mix
    lineup_whs: List[float] = []
    for i, bid in enumerate(batter_ids):
        w = weights[i] if i < len(weights) else 0.8
        brows = b_df[b_df["player_id"] == int(bid)]
        exp = 0.0
        u_sum = 0.0
        for _, prow in p_df.iterrows():
            pt = str(prow.get("pitch_type") or "").strip()
            if not pt:
                continue
            try:
                usage = float(prow.get("pitch_usage") or 0) / 100.0
            except (TypeError, ValueError):
                continue
            if usage <= 0:
                continue
            br = brows[brows["pitch_type"] == pt]
            if not br.empty:
                try:
                    bwh = float(br.iloc[0]["whiff_percent"])
                except (TypeError, ValueError):
                    bwh = LG_WHIFF
            else:
                bwh = LG_WHIFF
            exp += usage * bwh
            u_sum += usage
        if u_sum > 0 and u_sum < 0.99:
            exp = exp * (1.0 / u_sum)
        lineup_whs.append(w * exp)

    weighted_whiff = sum(lineup_whs) / max(0.001, total_w)

    # Scale to K% adjustment: higher whiff expectation vs this mix → more Ks
    k_pct_adj = (weighted_whiff - LG_WHIFF) * 0.14
    k_pct_adj = max(-2.8, min(2.8, k_pct_adj))

    return {
        "ok": True,
        "season": used_year,
        "expectedWhiffVsMix": round(weighted_whiff, 2),
        "kPctAdj": round(k_pct_adj, 3),
        "pitchMix": mix_rows[:8],
        "baselineWhiff": LG_WHIFF,
    }
