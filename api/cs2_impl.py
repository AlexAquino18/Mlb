"""
CS2 prop scanner — PrizePicks vs Underdog (+ Polymarket series).
Used by Vercel /api/cs2 and local server.py.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, Optional

_LIB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lib", "cs2_scanner"))
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

if os.environ.get("VERCEL"):
    os.environ.setdefault("CS2_DB_PATH", "/tmp/cs2_props.db")
    os.environ.setdefault("UNDERDOG_TIMEOUT_SECONDS", "20")

CACHE_TTL_SEC = 15 * 60
_CACHE: Dict[str, tuple] = {}


def _board_key(date: str, threshold: float) -> str:
    return f"{date}|{threshold:.2f}"


def cache_control(payload: Dict[str, Any], refresh: bool = False) -> str:
    if refresh or not payload.get("ok"):
        return "no-store"
    status = payload.get("status") or {}
    if not status.get("has_data") and not payload.get("matches"):
        return "no-store"
    return "public, max-age=60, s-maxage=180, stale-while-revalidate=300"


def ensure_snapshot(force: bool = False) -> Dict[str, Any]:
    import ingest
    import store

    store.init_db()
    st = ingest.status()
    if not force and ingest.snapshot_is_fresh(st):
        return st
    return ingest.run_ingest(force=force)


def get_dashboard(
    date: Optional[str] = None,
    threshold: float = 0.5,
    refresh: bool = False,
) -> Dict[str, Any]:
    import board

    status = ensure_snapshot(force=refresh)
    day = (date or "")[:10]
    key = _board_key(day, threshold)
    now = time.time()
    if not refresh and key in _CACHE:
        ts, payload = _CACHE[key]
        if now - ts < CACHE_TTL_SEC and payload.get("ok") is not False:
            payload = dict(payload)
            payload["status"] = status
            return payload
    payload = board.build_dashboard(date=day or None, threshold=threshold)
    if payload.get("ok") is not False:
        payload["ok"] = True
    payload["status"] = status
    if payload.get("ok"):
        _CACHE[key] = (now, payload)
    return payload
