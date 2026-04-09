"""
Statcast matchup: pitcher pitch mix × batter whiff% vs each pitch type.
Savant CSVs via requests (no pybaseball) — fits Vercel size limits.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from savant_http import batter_pitch_arsenal_stats, pitcher_pitch_arsenal_stats

# Module-level cache (warm per cold start)
_ARS_P: Dict[int, pd.DataFrame] = {}
_ARS_B: Dict[int, pd.DataFrame] = {}

LG_WHIFF = 24.0
LG_K_VS_PITCH = 22.0  # league-ish batter K% for missing pitch-type cells
# Expected whiff vs lineup (gap vs LG_WHIFF) → additive K% points in SO model — slightly stronger than raw gap so mix×whiff matters more
WHIFF_TO_KPCT = 0.19
KPCT_ADJ_CAP = 3.2
# Pitch-mix-weighted batter K% vs each pitch type (e.g. slider-heavy SP vs lineup that chases sliders) — complements whiff%
K_MIX_TO_KPCT = 0.11
KPCT_MIX_CAP = 1.65


def _load_arsenals(year: int):
    if year not in _ARS_P:
        try:
            _ARS_P[year] = pitcher_pitch_arsenal_stats(year, min_pa=10)
        except Exception:
            _ARS_P[year] = pd.DataFrame()

    if year not in _ARS_B:
        try:
            _ARS_B[year] = batter_pitch_arsenal_stats(year, min_pa=10)
        except Exception:
            _ARS_B[year] = pd.DataFrame()

    return _ARS_P.get(year), _ARS_B.get(year)


def compute_so_matchup(pitcher_mlbam: int, batter_ids: List[int], season: int) -> Dict[str, Any]:
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
        try:
            pm = p_all[p_all["player_id"] == int(pitcher_mlbam)]
        except Exception:
            continue
        if pm.empty:
            continue
        p_df = pm
        b_df = b_all
        used_year = yr
        break

    if p_df is None or b_df is None or p_df.empty:
        return {"ok": False, "error": "no_pitcher_arsenal", "seasonTried": season}

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

    def _lineup_pitch_blend(get_val) -> List[float]:
        out: List[float] = []
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
                val = get_val(br)
                exp += usage * val
                u_sum += usage
            if u_sum > 0 and u_sum < 0.99:
                exp = exp * (1.0 / u_sum)
            out.append(w * exp)
        return out

    def _whiff_from_row(br) -> float:
        if br.empty:
            return LG_WHIFF
        try:
            return float(br.iloc[0]["whiff_percent"])
        except (TypeError, ValueError, KeyError):
            return LG_WHIFF

    def _k_pct_from_row(br) -> float:
        if br.empty:
            return LG_K_VS_PITCH
        try:
            row = br.iloc[0]
            v = row["k_percent"] if "k_percent" in row.index else None
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return LG_K_VS_PITCH
            return float(v)
        except (TypeError, ValueError, KeyError):
            return LG_K_VS_PITCH

    lineup_whs = _lineup_pitch_blend(_whiff_from_row)
    lineup_k = _lineup_pitch_blend(_k_pct_from_row)

    weighted_whiff = sum(lineup_whs) / max(0.001, total_w)
    weighted_k_mix = sum(lineup_k) / max(0.001, total_w)

    k_pct_adj_whiff = (weighted_whiff - LG_WHIFF) * WHIFF_TO_KPCT
    k_pct_adj_whiff = max(-KPCT_ADJ_CAP, min(KPCT_ADJ_CAP, k_pct_adj_whiff))

    k_mix_adj = (weighted_k_mix - LG_K_VS_PITCH) * K_MIX_TO_KPCT
    k_mix_adj = max(-KPCT_MIX_CAP, min(KPCT_MIX_CAP, k_mix_adj))

    k_pct_adj = k_pct_adj_whiff + k_mix_adj
    k_pct_adj = max(-4.0, min(4.0, k_pct_adj))

    return {
        "ok": True,
        "season": used_year,
        "expectedWhiffVsMix": round(weighted_whiff, 2),
        "expectedKVsMix": round(weighted_k_mix, 2),
        "kPctAdj": round(k_pct_adj, 3),
        "kPctAdjWhiff": round(k_pct_adj_whiff, 3),
        "kPctAdjMixK": round(k_mix_adj, 3),
        "pitchMix": mix_rows[:8],
        "baselineWhiff": LG_WHIFF,
        "baselineKMix": LG_K_VS_PITCH,
    }
