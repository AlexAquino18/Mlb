"""Fetch live books and persist a snapshot."""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import requests

import config
import store
from sources import prizepicks, underdog

_LOCK = threading.Lock()
_STATE = {
    "running": False,
    "error": None,
    "last_snapshot_id": None,
    "last_counts": {},
}


def status() -> dict:
    latest = store.latest_snapshot_ids(1)
    created = latest[0][1] if latest else None
    return {
        "running": _STATE["running"],
        "error": _STATE["error"],
        "last_snapshot_id": _STATE["last_snapshot_id"] or (latest[0][0] if latest else None),
        "last_snapshot_at": created,
        "last_counts": _STATE["last_counts"],
        "has_data": bool(latest),
    }


def snapshot_is_fresh(st: dict | None = None) -> bool:
    row = st or status()
    if not row.get("has_data"):
        return False
    at = row.get("last_snapshot_at") or ""
    try:
        dt = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
        return age < config.INGEST_TTL_SECONDS
    except Exception:
        return True


def run_ingest(force: bool = False) -> dict:
    if not _LOCK.acquire(timeout=120):
        _STATE["error"] = "Timed out waiting for an in-flight refresh"
        return status()
    try:
        store.init_db()
        if not force and snapshot_is_fresh():
            return status()
        _STATE["running"] = True
        _STATE["error"] = None
        session = requests.Session()
        counts = {}
        props = []
        for name, fetcher in (
            ("prizepicks", prizepicks.fetch),
            ("underdog", underdog.fetch),
        ):
            try:
                batch = fetcher(session)
            except Exception as exc:
                counts[name] = f"error: {exc}"
                continue
            counts[name] = len(batch)
            props.extend(batch)
        snapshot_id = store.save_snapshot(props)
        try:
            from sources import polymarket

            counts["polymarket"] = len(polymarket.get_series_odds(session))
        except Exception as exc:
            counts["polymarket"] = f"error: {exc}"
        _STATE["last_snapshot_id"] = snapshot_id
        _STATE["last_counts"] = counts
        return status()
    except Exception as exc:
        _STATE["error"] = str(exc)
        raise
    finally:
        _STATE["running"] = False
        _LOCK.release()
