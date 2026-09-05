from __future__ import annotations

import os
import threading
import time
from typing import Any

_score_cache: dict[str, tuple[float, str | None, dict[str, Any]]] = {}
_score_cache_lock = threading.RLock()
SCORE_CACHE_TTL_SECONDS = int(os.getenv("SCORE_CACHE_TTL_SECONDS", "300"))
SCORE_CACHE_MAXSIZE = int(os.getenv("SCORE_CACHE_MAXSIZE", "600"))

_practical_cache: dict[str, tuple[float, str | None, float | None, dict[str, Any]]] = {}
_practical_cache_lock = threading.RLock()
PRACTICAL_CACHE_TTL_SECONDS = int(os.getenv("PRACTICAL_CACHE_TTL_SECONDS", "30"))
PRACTICAL_CACHE_MAXSIZE = int(os.getenv("PRACTICAL_CACHE_MAXSIZE", "600"))

_row_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_row_cache_lock = threading.RLock()
ROW_CACHE_TTL_TW50_SECONDS = int(os.getenv("ROW_CACHE_TTL_TW50_SECONDS", "300"))
ROW_CACHE_TTL_WATCH_SECONDS = int(os.getenv("ROW_CACHE_TTL_WATCH_SECONDS", "30"))
ROW_CACHE_MAXSIZE = int(os.getenv("ROW_CACHE_MAXSIZE", "600"))


def prune_timed_cache(cache: dict, ttl_seconds: float, maxsize: int, *, now_ts: float | None = None) -> int:
    """Prune simple {key: (timestamp, ...)} caches by TTL and max size."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    removed = 0
    for key, value in list(cache.items()):
        try:
            ts = float(value[0])
        except Exception:
            ts = 0.0
        if ttl_seconds >= 0 and now_ts - ts > ttl_seconds:
            cache.pop(key, None)
            removed += 1
    if maxsize > 0 and len(cache) > maxsize:
        overflow = len(cache) - maxsize
        sorted_items = sorted(cache.items(), key=lambda kv: float((kv[1] or (0,))[0] or 0))
        for key, _ in sorted_items[:overflow]:
            cache.pop(key, None)
            removed += 1
    return removed
