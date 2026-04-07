"""Re-export — implementation lives in api/fangraphs_impl.py."""
import os
import sys

_api = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "api"))
if _api not in sys.path:
    sys.path.insert(0, _api)

from fangraphs_impl import get_pitcher_advanced  # noqa: E402

__all__ = ["get_pitcher_advanced"]
