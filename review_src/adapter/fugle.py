from __future__ import annotations

import os
import threading
import time
from typing import Any

from core.config import FUGLE_API_KEY, FUGLE_BASE, FUGLE_WATCH_POLL_SECONDS, HEADERS, fugle_key_variants, safe_error
from core.http import request_json
from core.status import set_status
from core.utils import normalize_date, parse_num

_fugle_quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_fugle_lock = threading.RLock()
FUGLE_MIN_INTERVAL_SECONDS = float(os.getenv("FUGLE_MIN_INTERVAL_SECONDS", "0.3"))
FUGLE_QUOTE_TIMEOUT_SECONDS = float(os.getenv("FUGLE_QUOTE_TIMEOUT_SECONDS", "8"))


class FugleRateLimiter:
    """Small thread-safe rate limiter for Fugle free API.

    It reserves the next call slot under a private lock, then returns how long
    the caller should sleep outside the lock. This avoids exposing a module-level
    last-call timestamp and prevents lock-held sleep.

    Rate limiting counts every call attempt, including failed requests. This is
    intentional: if the remote API is down or timing out, failed attempts still
    consume a scheduled slot to prevent burst retries from exceeding the free
    Fugle quota.
    """
    def __init__(self, min_interval: float):
        self.min_interval = float(min_interval)
        self._last_call_ts = 0.0
        self._lock = threading.RLock()

    def reserve(self) -> float:
        with self._lock:
            now = time.time()
            scheduled_ts = max(now, self._last_call_ts + self.min_interval)
            wait_seconds = max(0.0, scheduled_ts - now)
            self._last_call_ts = scheduled_ts
            return wait_seconds


def fugle_headers() -> dict[str, str]:
    return {**HEADERS, "X-API-KEY": FUGLE_API_KEY}


def get_fugle_quote_cached(code: str) -> dict[str, Any] | None:
    """Return cached Fugle quote without making a network call."""
    code = str(code).zfill(4)
    ttl = max(3, max(10, FUGLE_WATCH_POLL_SECONDS) + 2)
    now = time.time()
    with _fugle_lock:
        cached = _fugle_quote_cache.get(code)
        if cached and now - cached[0] < ttl:
            return cached[1]
    return None


def fetch_fugle_quote_network(code: str) -> dict[str, Any] | None:
    # Call Fugle quote API only when an explicit network refresh is requested.
    if not FUGLE_API_KEY:
        return None
    code = str(code).zfill(4)

    # Reserve a Fugle API call slot, then sleep outside the limiter lock.
    wait_seconds = _fugle_rate_limiter.reserve()
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    url = f"{FUGLE_BASE}/intraday/quote/{code}"
    try:
        data = request_json(url, headers=fugle_headers(), retries=1, timeout=FUGLE_QUOTE_TIMEOUT_SECONDS)
        if isinstance(data, dict):
            with _fugle_lock:
                _fugle_quote_cache[code] = (time.time(), data)
            return data
        return None
    except Exception as exc:
        set_status("fugle", "stale", f"Fugle quote {code} failed: {safe_error(exc)}")
        return None


def fetch_fugle_price_volume_network(code: str) -> dict[str, Any] | None:
    if not FUGLE_API_KEY:
        set_status("price_volume", "stale", "Fugle API key is not configured; price-volume update skipped")
        return None
    code = str(code).zfill(4)
    wait_seconds = _fugle_rate_limiter.reserve()
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    url = f"{FUGLE_BASE}/intraday/volumes/{code}"
    last_exc: Exception | None = None
    for candidate in fugle_key_variants(FUGLE_API_KEY):
        try:
            data = request_json(
                url,
                headers={**HEADERS, "X-API-KEY": candidate},
                retries=1,
                timeout=FUGLE_QUOTE_TIMEOUT_SECONDS,
            )
            return data if isinstance(data, dict) else None
        except Exception as exc:
            last_exc = exc
            if "HTTP 401" not in str(exc):
                break
    err = safe_error(last_exc) if last_exc else "unknown error"
    set_status("price_volume", "stale", f"Fugle price-volume {code} failed: {err}")
    return None


def extract_fugle_price_volume_rows(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    # Accept documented Fugle response shapes and conservative wrappers.
    if not isinstance(payload, dict):
        return [], None
    trade_date = normalize_date(payload.get("date") or payload.get("tradeDate"))
    data = payload.get("data")
    if isinstance(data, dict):
        trade_date = trade_date or normalize_date(data.get("date") or data.get("tradeDate"))
        data = data.get("data") or data.get("items") or data.get("volumes")
    if data is None:
        data = payload.get("items") or payload.get("volumes") or payload.get("priceVolumes")
    if not isinstance(data, list):
        return [], trade_date
    rows: list[dict[str, Any]] = []
    for r in data:
        if not isinstance(r, dict):
            continue
        price = r.get("price") or r.get("tradePrice")
        volume = r.get("volume") or r.get("tradeVolume") or r.get("totalVolume")
        rows.append(
            {
                "price": price,
                "volume": volume,
                "volumeAtBid": r.get("volumeAtBid"),
                "volumeAtAsk": r.get("volumeAtAsk"),
            }
        )
    return rows, trade_date



_fugle_rate_limiter = FugleRateLimiter(FUGLE_MIN_INTERVAL_SECONDS)
