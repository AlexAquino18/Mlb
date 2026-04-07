"""
Direct Baseball Savant CSV downloads (no pybaseball) — keeps Vercel bundle under Lambda size limits.
"""
from __future__ import annotations

import io
import pandas as pd
import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*;q=0.9",
}


def fetch_csv(url: str, timeout: int = 55) -> pd.DataFrame:
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text))


def pitcher_pitch_arsenal_stats(year: int, min_pa: int = 0) -> pd.DataFrame:
    u = (
        f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
        f"?type=pitcher&pitchType=&year={year}&team=&min={min_pa}&csv=true"
    )
    return fetch_csv(u)


def batter_pitch_arsenal_stats(year: int, min_pa: int = 10) -> pd.DataFrame:
    u = (
        f"https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats"
        f"?type=batter&pitchType=&year={year}&team=&min={min_pa}&csv=true"
    )
    return fetch_csv(u)


def batter_expected_stats(year: int, min_pa: int = 0) -> pd.DataFrame:
    u = (
        f"https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=batter&year={year}&position=&team=&min={min_pa}&csv=true"
    )
    return fetch_csv(u)


def batter_exitvelo_barrels(year: int, min_bbe: int = 0) -> pd.DataFrame:
    u = (
        f"https://baseballsavant.mlb.com/leaderboard/statcast"
        f"?type=batter&year={year}&position=&team=&min={min_bbe}&csv=true"
    )
    return fetch_csv(u)
