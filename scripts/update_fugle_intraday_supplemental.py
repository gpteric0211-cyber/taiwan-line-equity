from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import requests
except Exception as exc:  # pragma: no cover - startup environment branch
    requests = None  # type: ignore[assignment]
    REQUESTS_IMPORT_ERROR = exc
else:
    REQUESTS_IMPORT_ERROR = None

try:
    import certifi
except Exception:  # pragma: no cover - optional dependency branch
    certifi = None  # type: ignore[assignment]

try:
    import truststore
except Exception:  # pragma: no cover - optional dependency branch
    truststore = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = REPO_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.mis import get_current_session_evidence  # noqa: E402
from adapter.twse_calendar import refresh_twse_holiday_cache  # noqa: E402
from core.config import FUGLE_API_KEY, HEADERS, fugle_key_variants, safe_error  # noqa: E402
from core.db import db  # noqa: E402
from core.fugle_intraday_schema import (  # noqa: E402
    FUGLE_BID_ASK_MAPPING_NOTE,
    FUGLE_TRADE_SIDE_NOTE,
    ensure_fugle_intraday_schema,
    prune_fugle_intraday_data,
    upsert_fugle_capture_run,
    upsert_fugle_bid_ask_summary,
    upsert_fugle_price_volume,
    upsert_fugle_trade,
)
from core.market_calendar_cache import taiwan_market_day_status  # noqa: E402


TPE = ZoneInfo("Asia/Taipei")
DEFAULT_CODES = ["2317", "3491", "2382"]
DEFAULT_API_BASE = "https://api.fugle.tw/marketdata/v1.0"
PARTIAL_RETRYABLE = 4
SOURCE_DELAYED_RETRYABLE = 5
DEFAULT_REQUESTS_PER_MINUTE = 50
DEFAULT_REQUEST_MAX_RETRIES = 2
DEFAULT_REQUEST_RETRY_WAIT_SECONDS = 60.0
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
SIDE_LABELS_ZH = {
    "ASK": "外盤",
    "BID": "內盤",
    "MID": "中性",
    "UNKNOWN": "無法判斷",
    "INVALID_QUOTE": "報價異常",
}
VALID_SIDE_VALUES = set(SIDE_LABELS_ZH)
VALID_SIDE_METHODS = {"PRICE_VS_BID_ASK", "PRICE_TICK_FROM_PREV_TRADE", "UNKNOWN"}
VALID_SIDE_CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
VALID_PREV_PRICE_SOURCES = {"PREVIOUS_CLOSE", "PREVIOUS_TRADE", "NOT_USED", "UNKNOWN"}
ENDPOINTS = {
    "trades": "/stock/intraday/trades/{symbol}",
    "volumes": "/stock/intraday/volumes/{symbol}",
    "quote": "/stock/intraday/quote/{symbol}",
}


def now_tpe() -> datetime:
    return datetime.now(TPE)


class RollingRequestLimiter:
    """Keep every real Fugle HTTP request inside one rolling minute budget."""

    def __init__(self, requests_per_minute: int, *, window_seconds: float = 60.0):
        self.requests_per_minute = max(0, int(requests_per_minute))
        self.window_seconds = max(0.01, float(window_seconds))
        self._timestamps: deque[float] = deque()
        self.request_count = 0
        self.throttle_wait_count = 0
        self.throttle_sleep_seconds = 0.0

    def wait(self) -> None:
        if self.requests_per_minute <= 0:
            self.request_count += 1
            return
        throttle_recorded = False
        while True:
            current = time.monotonic()
            cutoff = current - self.window_seconds
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) < self.requests_per_minute:
                self._timestamps.append(current)
                self.request_count += 1
                return
            delay = max(self._timestamps[0] + self.window_seconds - current, 0.01)
            if not throttle_recorded:
                self.throttle_wait_count += 1
                throttle_recorded = True
            self.throttle_sleep_seconds += delay
            time.sleep(delay)

    def summary(self) -> dict[str, Any]:
        return {
            "requests_per_minute": self.requests_per_minute,
            "request_count": self.request_count,
            "throttle_wait_count": self.throttle_wait_count,
            "throttle_sleep_seconds": round(self.throttle_sleep_seconds, 3),
        }


def retry_after_seconds(response: Any, fallback_seconds: float) -> float:
    """Read a numeric Retry-After header without trusting arbitrary text."""

    headers = getattr(response, "headers", {}) or {}
    raw_value = str(headers.get("Retry-After") or "").strip()
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        parsed = float(fallback_seconds)
    return max(0.0, parsed)


def latest_completed_trading_date(as_of: datetime) -> tuple[str | None, dict[str, Any]]:
    """Resolve the latest completed Taiwan trading date from verified calendar data."""

    local_now = as_of.astimezone(TPE)
    candidate = local_now.date()
    today_status = taiwan_market_day_status(candidate)
    minutes = local_now.hour * 60 + local_now.minute
    if today_status.get("is_trading_day") and minutes <= 13 * 60 + 30:
        candidate -= timedelta(days=1)

    for _ in range(370):
        status = taiwan_market_day_status(candidate)
        if not status.get("verified"):
            return None, status
        if status.get("is_trading_day"):
            return candidate.isoformat(), status
        candidate -= timedelta(days=1)
    return None, {
        "date": candidate.isoformat(),
        "is_trading_day": False,
        "verified": False,
        "source": "search_exhausted",
        "reason": "no verified completed trading date found",
    }


def iso_now_text() -> str:
    return now_tpe().strftime("%Y-%m-%d %H:%M:%S")


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def normalize_date_text(value: Any) -> str:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return ""


def normalize_codes(raw: str | None) -> list[str]:
    codes: list[str] = []
    for part in str(raw or ",".join(DEFAULT_CODES)).replace(";", ",").split(","):
        code = normalize_code(part)
        if code and code not in codes:
            codes.append(code)
    return codes or list(DEFAULT_CODES)


def normalize_optional_codes(raw: str | None) -> list[str]:
    codes: list[str] = []
    for part in re.split(r"[,;\s]+", str(raw or "")):
        code = normalize_code(part)
        if code and code.isdigit() and code not in codes:
            codes.append(code)
    return codes


def hhmm_minutes(value: str) -> int:
    parsed = datetime.strptime(str(value), "%H:%M")
    return parsed.hour * 60 + parsed.minute


def capture_window_deadline(window_end: str, *, reference: datetime | None = None) -> datetime:
    """Return the fixed Taipei deadline for one capture invocation."""

    current = reference or now_tpe()
    parsed = datetime.strptime(str(window_end), "%H:%M")
    return current.replace(
        hour=parsed.hour,
        minute=parsed.minute,
        second=0,
        microsecond=0,
    )


def capture_window_expired(deadline: datetime) -> bool:
    """Prevent a long paginated capture from issuing requests past its window."""

    return now_tpe() > deadline


def fugle_market_preflight(
    *,
    requested_date: str | None,
    evidence_codes: list[str] | None,
    evidence_max_age_seconds: int,
    write_calendar_cache: bool,
    window_start: str = "09:00",
    window_end: str = "13:30",
    capture_phase: str = "intraday",
) -> dict[str, Any]:
    """Fail closed before any Fugle request.

    Intraday capture requires live MIS evidence.  Post-close capture requires a
    verified official calendar and still relies on explicit same-day provider
    dates plus later official EOD-volume reconciliation before becoming usable.
    """

    now = now_tpe()
    today = now.date().isoformat()
    local_day_status = taiwan_market_day_status(now.date())
    base = {
        "ok": False,
        "today": today,
        "as_of": now.isoformat(timespec="seconds"),
        "calendar": local_day_status,
        "calendar_refresh": None,
        "mis_evidence": None,
        "capture_phase": capture_phase,
        "fugle_api_allowed": False,
        "writes_db": False,
    }
    if capture_phase not in {"intraday", "post_close", "latest_completed"}:
        return {
            **base,
            "status": "INVALID_CAPTURE_PHASE",
            "clean_skip": False,
            "reason": f"unsupported capture phase {capture_phase}",
        }
    if capture_phase == "latest_completed":
        calendar_refresh = refresh_twse_holiday_cache(
            required_year=now.year,
            write_cache=write_calendar_cache,
        )
        expected_date, expected_status = latest_completed_trading_date(now)
        base["calendar_refresh"] = calendar_refresh
        base["expected_date"] = expected_date
        base["expected_date_calendar"] = expected_status
        if not expected_date:
            return {
                **base,
                "status": "CALENDAR_UNVERIFIED",
                "clean_skip": False,
                "reason": "latest completed trading date cannot be verified",
            }
        if requested_date and requested_date != expected_date:
            return {
                **base,
                "status": "SOURCE_DELAYED",
                "clean_skip": False,
                "reason": f"requested date {requested_date} is not latest completed trading date {expected_date}",
            }
        now_minutes = now.hour * 60 + now.minute
        if (
            local_day_status.get("is_trading_day")
            and 9 * 60 <= now_minutes <= 13 * 60 + 30
        ):
            return {
                **base,
                "ok": True,
                "status": "SKIPPED_BEFORE_MARKET_CLOSE",
                "clean_skip": True,
                "reason": "latest-completed capture waits until the current session has closed",
            }
        return {
            **base,
            "ok": True,
            "status": "OK",
            "clean_skip": False,
            "fugle_api_allowed": True,
            "reason": f"verified latest completed trading date {expected_date}",
        }
    if requested_date and requested_date != today:
        return {
            **base,
            "status": "SOURCE_DELAYED",
            "clean_skip": False,
            "reason": f"requested date {requested_date} is not current Taipei date {today}",
        }
    if not local_day_status.get("is_trading_day"):
        return {
            **base,
            "ok": True,
            "status": "SKIPPED_MARKET_CLOSED",
            "clean_skip": True,
            "reason": str(local_day_status.get("reason") or "market_closed"),
        }
    try:
        start_minutes = hhmm_minutes(window_start)
        end_minutes = hhmm_minutes(window_end)
    except ValueError:
        return {
            **base,
            "status": "INVALID_CAPTURE_WINDOW",
            "clean_skip": False,
            "reason": f"invalid capture window {window_start}-{window_end}",
        }
    now_minutes = now.hour * 60 + now.minute
    if start_minutes > end_minutes or not start_minutes <= now_minutes <= end_minutes:
        return {
            **base,
            "ok": True,
            "status": "SKIPPED_OUTSIDE_CAPTURE_WINDOW",
            "clean_skip": True,
            "reason": f"current time is outside capture window {window_start}-{window_end}",
        }

    calendar_refresh = refresh_twse_holiday_cache(
        required_year=now.year,
        write_cache=write_calendar_cache,
    )
    refreshed_day_status = taiwan_market_day_status(now.date())
    base["calendar_refresh"] = calendar_refresh
    base["calendar"] = refreshed_day_status
    if not refreshed_day_status.get("is_trading_day"):
        return {
            **base,
            "ok": True,
            "status": "SKIPPED_MARKET_CLOSED",
            "clean_skip": True,
            "reason": str(refreshed_day_status.get("reason") or "market_closed"),
        }
    if not refreshed_day_status.get("verified"):
        return {
            **base,
            "status": "CALENDAR_UNVERIFIED",
            "clean_skip": False,
            "reason": "official calendar year is unavailable; Fugle request blocked",
        }

    if capture_phase == "post_close":
        if now_minutes <= 13 * 60 + 30:
            return {
                **base,
                "ok": True,
                "status": "SKIPPED_BEFORE_MARKET_CLOSE",
                "clean_skip": True,
                "reason": "post-close capture starts after 13:30 Asia/Taipei",
            }
        return {
            **base,
            "ok": True,
            "status": "OK",
            "clean_skip": False,
            "reason": "official calendar passed; provider date and official EOD volume must still reconcile",
            "fugle_api_allowed": True,
        }

    mis_evidence = get_current_session_evidence(
        evidence_codes or None,
        as_of=now,
        max_age_seconds=evidence_max_age_seconds,
    )
    base["mis_evidence"] = mis_evidence
    if not mis_evidence.get("ok"):
        return {
            **base,
            "status": str(mis_evidence.get("status") or "SOURCE_DELAYED"),
            "clean_skip": bool(mis_evidence.get("clean_skip")),
            "reason": str(mis_evidence.get("reason") or "current TWSE MIS session evidence unavailable"),
        }
    return {
        **base,
        "ok": True,
        "status": "OK",
        "clean_skip": False,
        "reason": "official calendar and current TWSE MIS session evidence passed",
        "fugle_api_allowed": True,
    }


def num_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def num_int(value: Any) -> int | None:
    number = num_float(value)
    if number is None:
        return None
    return int(round(number))


def mask_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "not configured"
    return f"****{text[-4:]}" if len(text) > 4 else "****"


def sanitize_error(value: Any, key_variants: list[str]) -> str:
    text = safe_error(value)
    for key in key_variants:
        if key:
            text = text.replace(key, "***")
    return text


def ca_bundle_candidates() -> list[tuple[str, bool | str]]:
    candidates: list[tuple[str, bool | str]] = [("default", True)]
    seen: set[str] = set()

    def add_path(label: str, value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        path = Path(text)
        if not path.exists():
            return
        normalized = str(path.resolve()).lower()
        if normalized in seen:
            return
        seen.add(normalized)
        candidates.append((label, str(path)))

    add_path("REQUESTS_CA_BUNDLE", os.getenv("REQUESTS_CA_BUNDLE"))
    add_path("SSL_CERT_FILE", os.getenv("SSL_CERT_FILE"))
    if certifi is not None:
        try:
            add_path("certifi", certifi.where())
        except Exception:
            pass

    appdata = os.getenv("APPDATA")
    if appdata:
        pattern = str(Path(appdata) / "Python" / "Python*" / "site-packages" / "certifi" / "cacert.pem")
        for path_text in glob.glob(pattern):
            add_path("user_site_certifi", path_text)
    return candidates


def enable_system_truststore() -> bool:
    if truststore is None:
        return False
    try:
        truststore.inject_into_ssl()
        return True
    except Exception:
        return False


def load_api_key() -> tuple[str, list[str], str]:
    configured = str(FUGLE_API_KEY or "").strip()
    source = "review_src.core.config"
    if not configured:
        configured = str(os.getenv("FUGLE_API_KEY", "") or "").strip()
        source = "environment"
    variants = fugle_key_variants(configured)
    return configured, variants, source


def payload_root(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data
    return payload


def payload_rows(payload: Any) -> list[dict[str, Any]]:
    root = payload_root(payload)
    data = root.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    for key in ("items", "trades", "volumes", "priceVolumes"):
        rows = root.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def payload_date(payload: Any) -> str:
    root = payload_root(payload)
    for key in ("date", "tradeDate"):
        value = root.get(key)
        if value:
            return str(value)
    if isinstance(payload, dict):
        for key in ("date", "tradeDate"):
            value = payload.get(key)
            if value:
                return str(value)
    return ""


def payload_symbol(payload: Any) -> str:
    root = payload_root(payload)
    for key in ("symbol", "code"):
        value = root.get(key)
        if value:
            return str(value)
    if isinstance(payload, dict):
        for key in ("symbol", "code"):
            value = payload.get(key)
            if value:
                return str(value)
    return ""


def fetch_endpoint(
    *,
    api_base: str,
    endpoint: str,
    code: str,
    key_variants: list[str],
    timeout: float,
    params: dict[str, Any] | None = None,
    rate_limiter: RollingRequestLimiter | None = None,
    max_retries: int = DEFAULT_REQUEST_MAX_RETRIES,
    retry_wait_seconds: float = DEFAULT_REQUEST_RETRY_WAIT_SECONDS,
    deadline: datetime | None = None,
) -> dict[str, Any]:
    if requests is None:
        return {
            "ok": False,
            "http_status": None,
            "json_ok": False,
            "error_type": "REQUESTS_IMPORT_ERROR",
            "error": safe_error(REQUESTS_IMPORT_ERROR) if REQUESTS_IMPORT_ERROR else "requests import failed",
            "payload": None,
        }
    url = api_base.rstrip("/") + ENDPOINTS[endpoint].format(symbol=code)
    last_error = ""
    last_result: dict[str, Any] | None = None
    start = time.time()
    truststore_enabled = enable_system_truststore()
    verify_modes = ca_bundle_candidates()
    if truststore_enabled:
        verify_modes = [("system_truststore", True)] + [mode for mode in verify_modes if mode[0] != "default"]
    attempts = max(0, int(max_retries)) + 1
    for attempt in range(attempts):
        should_retry = False
        retry_delay = max(0.0, float(retry_wait_seconds))
        for key in key_variants:
            for verify_mode, verify_value in verify_modes:
                if deadline is not None and capture_window_expired(deadline):
                    return {
                        "ok": False,
                        "http_status": None,
                        "json_ok": False,
                        "error_type": "CAPTURE_WINDOW_EXPIRED",
                        "error": "capture window ended before the next provider request",
                        "payload": None,
                        "url": url,
                    }
                try:
                    if rate_limiter is not None:
                        rate_limiter.wait()
                    response = requests.get(
                        url,
                        headers={**HEADERS, "X-API-KEY": key, "User-Agent": "Taiwan50Dashboard/FugleSupplemental"},
                        params=params,
                        timeout=timeout,
                        verify=verify_value,
                    )
                    elapsed_ms = round((time.time() - start) * 1000, 1)
                    payload: Any = None
                    json_ok = False
                    parse_error = ""
                    try:
                        payload = response.json()
                        json_ok = True
                    except Exception as exc:
                        parse_error = sanitize_error(exc, key_variants)
                    status = int(response.status_code)
                    last_result = {
                        "ok": 200 <= status < 300,
                        "http_status": status,
                        "json_ok": json_ok,
                        "json_parse_error": parse_error,
                        "payload": payload,
                        "url": url,
                        "elapsed_ms": elapsed_ms,
                        "tls_verify_mode": verify_mode,
                        "error_type": "RATE_LIMITED" if status == 429 else ("TRANSIENT_HTTP_ERROR" if status in TRANSIENT_HTTP_STATUSES else ""),
                        "error": "" if 200 <= status < 300 else response.text[:300],
                    }
                    if status in TRANSIENT_HTTP_STATUSES and attempt + 1 < attempts:
                        should_retry = True
                        retry_delay = retry_after_seconds(response, retry_delay)
                        break
                    return last_result
                except requests.exceptions.SSLError as exc:  # type: ignore[union-attr]
                    last_error = sanitize_error(exc, key_variants)
                    continue
                except requests.exceptions.Timeout as exc:  # type: ignore[union-attr]
                    last_error = sanitize_error(exc, key_variants)
                    last_result = {"ok": False, "http_status": None, "json_ok": False, "error_type": "TIMEOUT", "error": last_error, "payload": None, "url": url}
                    should_retry = attempt + 1 < attempts
                    break
                except requests.exceptions.RequestException as exc:  # type: ignore[union-attr]
                    last_error = sanitize_error(exc, key_variants)
                    last_result = {"ok": False, "http_status": None, "json_ok": False, "error_type": "REQUEST_ERROR", "error": last_error, "payload": None, "url": url}
                    should_retry = attempt + 1 < attempts
                    break
            if should_retry:
                break
        if should_retry:
            time.sleep(retry_delay)
            continue
        if last_result is not None:
            return last_result
    return last_result or {"ok": False, "http_status": None, "json_ok": False, "error_type": "REQUEST_ERROR", "error": last_error or "request failed", "payload": None, "url": url}


def fetch_trades_paginated(
    *,
    api_base: str,
    code: str,
    key_variants: list[str],
    timeout: float,
    page_limit: int = 500,
    max_pages: int = 200,
    page_sleep_seconds: float = 0.0,
    rate_limiter: RollingRequestLimiter | None = None,
    max_retries: int = DEFAULT_REQUEST_MAX_RETRIES,
    retry_wait_seconds: float = DEFAULT_REQUEST_RETRY_WAIT_SECONDS,
    deadline: datetime | None = None,
) -> dict[str, Any]:
    """Fetch the documented trades collection until a short terminal page.

    Completeness is fail-closed: every page must carry the same explicit date
    and symbol, and reaching ``max_pages`` on a full page is never considered
    complete.
    """

    limit = max(1, min(int(page_limit), 1000))
    page_cap = max(1, min(int(max_pages), 1000))
    expected_date = ""
    expected_symbol = ""
    combined: list[dict[str, Any]] = []
    seen_serials: set[str] = set()
    last_response: dict[str, Any] = {}
    for page_index in range(page_cap):
        offset = page_index * limit
        response = fetch_endpoint(
            api_base=api_base,
            endpoint="trades",
            code=code,
            key_variants=key_variants,
            timeout=timeout,
            params={"offset": offset, "limit": limit, "sort": "asc"},
            rate_limiter=rate_limiter,
            max_retries=max_retries,
            retry_wait_seconds=retry_wait_seconds,
            deadline=deadline,
        )
        last_response = response
        if not response.get("ok") or not response.get("json_ok"):
            reason = str(response.get("reason") or response.get("error") or response.get("json_parse_error") or "trade page request failed")
            return {
                **response,
                "ok": False,
                "complete": False,
                "rows": combined,
                "response_date": expected_date,
                "response_symbol": expected_symbol,
                "page_count": page_index + 1,
                "reason": reason,
            }
        payload = response.get("payload")
        rows = payload_rows(payload)
        page_date = normalize_date_text(payload_date(payload))
        page_symbol = normalize_code(payload_symbol(payload))
        if not page_date:
            return {
                **response,
                "ok": False,
                "complete": False,
                "rows": combined,
                "response_date": expected_date,
                "response_symbol": expected_symbol,
                "page_count": page_index + 1,
                "error_type": "DATE_MISMATCH",
                "reason": "trade page date is missing or invalid",
            }
        if not page_symbol:
            return {
                **response,
                "ok": False,
                "complete": False,
                "rows": combined,
                "response_date": expected_date or page_date,
                "response_symbol": expected_symbol,
                "page_count": page_index + 1,
                "error_type": "SYMBOL_MISMATCH",
                "reason": "trade page symbol is missing or invalid",
            }
        if page_index == 0:
            expected_date = page_date
            expected_symbol = page_symbol
        elif page_date != expected_date:
            return {
                **response,
                "ok": False,
                "complete": False,
                "rows": combined,
                "response_date": expected_date,
                "response_symbol": expected_symbol,
                "page_count": page_index + 1,
                "error_type": "DATE_MISMATCH",
                "reason": f"trade page date mismatch: expected {expected_date}, got {page_date}",
            }
        elif page_symbol != expected_symbol:
            return {
                **response,
                "ok": False,
                "complete": False,
                "rows": combined,
                "response_date": expected_date,
                "response_symbol": expected_symbol,
                "page_count": page_index + 1,
                "error_type": "SYMBOL_MISMATCH",
                "reason": f"trade page symbol mismatch: expected {expected_symbol}, got {page_symbol}",
            }
        for row in rows:
            serial = str(row.get("serial") or "").strip()
            if serial and serial in seen_serials:
                continue
            if serial:
                seen_serials.add(serial)
            combined.append(row)
        if len(rows) < limit:
            combined_payload = {
                "date": expected_date,
                "symbol": expected_symbol,
                "data": combined,
            }
            return {
                **response,
                "ok": bool(combined),
                "complete": bool(combined),
                "payload": combined_payload,
                "rows": combined,
                "response_date": expected_date,
                "response_symbol": expected_symbol,
                "page_count": page_index + 1,
                "error_type": "" if combined else "EMPTY_TRADES",
                "reason": "" if combined else "trade pages contained no rows",
            }
        if page_sleep_seconds > 0 and page_index + 1 < page_cap:
            time.sleep(page_sleep_seconds)
    return {
        **last_response,
        "ok": False,
        "complete": False,
        "rows": combined,
        "response_date": expected_date,
        "response_symbol": expected_symbol,
        "page_count": page_cap,
        "error_type": "MAX_PAGES_EXCEEDED",
        "reason": f"trade pagination reached {page_cap} full pages without a terminal short page",
    }


def endpoint_quality(rows: list[dict[str, Any]], response_date: str, requested_date: str | None, today: str, allow_noncurrent: bool) -> tuple[str, bool, str]:
    if not rows:
        return "FAILED", False, "empty payload rows"
    if requested_date and response_date != requested_date:
        return "SOURCE_DELAYED", False, f"response date {response_date or '--'} does not match requested date {requested_date}"
    if not allow_noncurrent and response_date != today:
        return "SOURCE_DELAYED", False, f"response date {response_date or '--'} is not current date {today}"
    return "OK", True, ""


def previous_close_for_trade_date(conn: sqlite3.Connection, code: str, trade_date: str) -> float | None:
    row = conn.execute(
        """
        SELECT close
        FROM history_price
        WHERE code=? AND date < ? AND close IS NOT NULL
        ORDER BY date DESC
        LIMIT 1
        """,
        (normalize_code(code), trade_date),
    ).fetchone()
    return num_float(row[0]) if row else None


def _trade_time_key(value: Any) -> tuple[int, int, int, str]:
    text = str(value or "").strip()
    try:
        parts = [int(part) for part in text.split(":")[:3]]
        while len(parts) < 3:
            parts.append(0)
        return (parts[0], parts[1], parts[2], text)
    except Exception:
        return (99, 99, 99, text)


def _serial_sort_value(value: Any) -> tuple[int, Any]:
    text = str(value or "").strip()
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def ordering_note_for_trade_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "ordering_unverified"
    if all(row.get("_source_serial_present") for row in rows):
        return "ordered_by_time_serial"
    if all(row.get("trade_time") for row in rows):
        return "ordered_by_time_only"
    return "ordered_by_api_response"


def sort_trade_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    note = ordering_note_for_trade_rows(rows)
    if note == "ordered_by_time_serial":
        return sorted(rows, key=lambda row: (_trade_time_key(row.get("trade_time")), _serial_sort_value(row.get("serial")))), note
    if note == "ordered_by_time_only":
        return sorted(rows, key=lambda row: (_trade_time_key(row.get("trade_time")), int(row.get("_api_index") or 0))), note
    return sorted(rows, key=lambda row: int(row.get("_api_index") or 0)), note


def _side_result(
    side: str,
    method: str,
    confidence: str,
    reason: str,
    prev_price: float | None,
    prev_price_source: str,
) -> dict[str, Any]:
    return normalize_side_payload({
        "side_inferred": side,
        "side_label_zh": SIDE_LABELS_ZH.get(side, SIDE_LABELS_ZH["UNKNOWN"]),
        "side_method": method,
        "side_confidence": confidence,
        "side_reason": reason,
        "prev_price": prev_price,
        "prev_price_source": prev_price_source,
    })


def _clean_upper(value: Any, default: str) -> str:
    text = str(value or "").strip().upper()
    return text or default


def normalize_side_payload(row: dict[str, Any]) -> dict[str, Any]:
    side = _clean_upper(row.get("side_inferred"), "UNKNOWN")
    if side not in VALID_SIDE_VALUES:
        side = "UNKNOWN"
    method = _clean_upper(row.get("side_method"), "UNKNOWN")
    if method not in VALID_SIDE_METHODS:
        method = "UNKNOWN"
    confidence = _clean_upper(row.get("side_confidence"), "UNKNOWN")
    if confidence not in VALID_SIDE_CONFIDENCE:
        confidence = "UNKNOWN"
    prev_price_source = _clean_upper(row.get("prev_price_source"), "UNKNOWN")
    if prev_price_source not in VALID_PREV_PRICE_SOURCES:
        prev_price_source = "UNKNOWN"
    reason = str(row.get("side_reason") or "fallback unknown").strip() or "fallback unknown"
    label = str(row.get("side_label_zh") or SIDE_LABELS_ZH.get(side) or SIDE_LABELS_ZH["UNKNOWN"]).strip()
    if not label:
        label = SIDE_LABELS_ZH["UNKNOWN"]
    normalized = dict(row)
    normalized.update(
        {
            "side_inferred": side,
            "side_label_zh": label,
            "side_method": method,
            "side_confidence": confidence,
            "side_reason": reason,
            "prev_price_source": prev_price_source,
        }
    )
    return normalized


def infer_trade_side(
    *,
    price: float | None,
    bid: float | None,
    ask: float | None,
    prev_price: float | None,
    prev_price_source: str,
    previous_known_side: str | None,
    is_first_trade: bool,
) -> dict[str, Any]:
    if price is None or price <= 0:
        return _side_result("UNKNOWN", "UNKNOWN", "UNKNOWN", "missing price", None, "UNKNOWN")
    if bid is not None and ask is not None:
        if ask <= bid:
            return _side_result("INVALID_QUOTE", "PRICE_VS_BID_ASK", "LOW", "invalid bid/ask spread", None, "NOT_USED")
        if price >= ask:
            return _side_result("ASK", "PRICE_VS_BID_ASK", "HIGH", "price >= ask", None, "NOT_USED")
        if price <= bid:
            return _side_result("BID", "PRICE_VS_BID_ASK", "HIGH", "price <= bid", None, "NOT_USED")
        return _side_result("MID", "PRICE_VS_BID_ASK", "MEDIUM", "bid < price < ask", None, "NOT_USED")
    if prev_price is None:
        return _side_result("UNKNOWN", "PRICE_TICK_FROM_PREV_TRADE", "UNKNOWN", "missing previous close", None, "UNKNOWN")
    if price > prev_price:
        return _side_result("ASK", "PRICE_TICK_FROM_PREV_TRADE", "MEDIUM", "price > prev_price", prev_price, prev_price_source)
    if price < prev_price:
        return _side_result("BID", "PRICE_TICK_FROM_PREV_TRADE", "MEDIUM", "price < prev_price", prev_price, prev_price_source)
    if previous_known_side in {"ASK", "BID", "MID"}:
        return _side_result(previous_known_side, "PRICE_TICK_FROM_PREV_TRADE", "LOW", "price = prev_price, carry previous known side", prev_price, prev_price_source)
    if is_first_trade and prev_price_source == "PREVIOUS_CLOSE":
        return _side_result("MID", "PRICE_TICK_FROM_PREV_TRADE", "LOW", "first trade equals previous close", prev_price, prev_price_source)
    return _side_result("UNKNOWN", "PRICE_TICK_FROM_PREV_TRADE", "UNKNOWN", "fallback unknown", prev_price, prev_price_source)


def coverage_note_for_trade_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "unverified"
    times = [str(row.get("trade_time") or "") for row in rows if row.get("trade_time")]
    if not times:
        return "unverified"
    earliest = min(times)
    latest = max(times)
    if earliest <= "09:05:00" and latest >= "13:25:00":
        return "appears_regular_session_covered"
    return "partial_or_unverified"


def side_stats_for_trade_rows(rows: list[dict[str, Any]], *, ordering_note: str, previous_close: float | None) -> dict[str, Any]:
    null_side_count = sum(1 for row in rows if not str(row.get("side_inferred") or "").strip())
    null_method_count = sum(1 for row in rows if not str(row.get("side_method") or "").strip())
    side_counts = Counter(str(row.get("side_inferred")).strip().upper() for row in rows if str(row.get("side_inferred") or "").strip())
    method_counts = Counter(str(row.get("side_method")).strip().upper() for row in rows if str(row.get("side_method") or "").strip())
    times = [str(row.get("trade_time") or "") for row in rows if row.get("trade_time")]
    return {
        "trade_count": len(rows),
        "earliest_trade_time": min(times) if times else None,
        "latest_trade_time": max(times) if times else None,
        "coverage_note": coverage_note_for_trade_rows(rows),
        "ordering_note": ordering_note,
        "previous_close": previous_close,
        "null_side_count": null_side_count,
        "null_method_count": null_method_count,
        "side_counts": dict(sorted(side_counts.items())),
        "method_counts": dict(sorted(method_counts.items())),
        "unknown_count": int(side_counts.get("UNKNOWN") or 0),
        "unknown_method_count": int(method_counts.get("UNKNOWN") or 0),
        "invalid_quote_count": int(side_counts.get("INVALID_QUOTE") or 0),
    }


def apply_side_inference(rows: list[dict[str, Any]], previous_close: float | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ordered_rows, ordering_note = sort_trade_rows(rows)
    previous_trade_price: float | None = None
    previous_known_side: str | None = None
    for idx, row in enumerate(ordered_rows):
        price = num_float(row.get("price"))
        if idx == 0:
            prev_price = previous_close
            prev_price_source = "PREVIOUS_CLOSE" if previous_close is not None else "UNKNOWN"
        else:
            prev_price = previous_trade_price
            prev_price_source = "PREVIOUS_TRADE" if previous_trade_price is not None else "UNKNOWN"
        side = infer_trade_side(
            price=price,
            bid=num_float(row.get("bid")),
            ask=num_float(row.get("ask")),
            prev_price=prev_price,
            prev_price_source=prev_price_source,
            previous_known_side=previous_known_side,
            is_first_trade=idx == 0,
        )
        row.update(side)
        if row.get("side_inferred") in {"ASK", "BID", "MID"}:
            previous_known_side = str(row.get("side_inferred"))
        if price is not None:
            previous_trade_price = price
    return ordered_rows, side_stats_for_trade_rows(ordered_rows, ordering_note=ordering_note, previous_close=previous_close)


def normalize_fugle_trade_time(value: Any, trade_date: str | None = None) -> str:
    """Convert Fugle's documented Unix microsecond timestamp to Taipei time."""

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        raw = int(text)
    except (TypeError, ValueError):
        return ""
    try:
        if raw >= 10**14:
            seconds, micros = divmod(raw, 1_000_000)
        elif raw >= 10**11:
            seconds, millis = divmod(raw, 1_000)
            micros = millis * 1_000
        else:
            seconds, micros = raw, 0
        parsed = datetime.fromtimestamp(seconds, TPE).replace(microsecond=micros)
    except (OSError, OverflowError, ValueError):
        return ""
    expected_date = normalize_date_text(trade_date) if trade_date else ""
    if expected_date and parsed.date().isoformat() != expected_date:
        return ""
    return parsed.strftime("%H:%M:%S.%f")


def normalize_trade_rows(code: str, trade_date: str, rows: list[dict[str, Any]], data_quality: str, fetched_at: float) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        raw_trade_time = row.get("time") or row.get("tradeTime")
        trade_time = normalize_fugle_trade_time(raw_trade_time, trade_date=trade_date)
        price = num_float(row.get("price"))
        size = num_int(row.get("size"))
        volume = num_int(row.get("volume"))
        serial = str(row.get("serial") or "").strip()
        if not serial:
            serial = f"{trade_time}|{price}|{size}|{volume}|{idx}"
        if not trade_time or price is None:
            continue
        normalized.append(
            {
                "_api_index": idx,
                "_source_serial_present": bool(str(row.get("serial") or "").strip()),
                "code": code,
                "trade_date": trade_date,
                "trade_time": trade_time,
                "price": price,
                "size": size,
                "volume": volume,
                "bid": num_float(row.get("bid")),
                "ask": num_float(row.get("ask")),
                "serial": serial,
                "data_quality": data_quality,
                "fetched_at": fetched_at,
                "raw_json": row,
            }
        )
    return normalized


def normalize_volume_rows(code: str, trade_date: str, rows: list[dict[str, Any]], data_quality: str, fetched_at: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    total_volume = 0
    total_bid = 0
    total_ask = 0
    partial_missing_bid_ask = False
    snapshot_time = iso_now_text()
    for row in rows:
        price = num_float(row.get("price") or row.get("tradePrice"))
        volume = num_int(row.get("volume") or row.get("tradeVolume") or row.get("totalVolume"))
        bid = num_int(row.get("volumeAtBid"))
        ask = num_int(row.get("volumeAtAsk"))
        if price is None or volume is None:
            continue
        if bid is None or ask is None:
            partial_missing_bid_ask = True
        bid = bid or 0
        ask = ask or 0
        neutral = max(int(volume) - int(bid) - int(ask), 0)
        total_volume += int(volume)
        total_bid += int(bid)
        total_ask += int(ask)
        normalized.append(
            {
                "code": code,
                "trade_date": trade_date,
                "price": price,
                "volume_lots": int(volume),
                "volume_at_bid": int(bid),
                "volume_at_ask": int(ask),
                "neutral_volume_lots": neutral,
                "total_volume_lots": None,
                # The scheduled job runs before the official close is available.
                # It is a real Fugle snapshot, but not yet EOD-reconciled.
                "data_quality": "PARTIAL" if partial_missing_bid_ask else "INTRADAY_SNAPSHOT",
                "fetched_at": fetched_at,
                "snapshot_time": snapshot_time,
            }
        )
    summary_quality = "PARTIAL" if partial_missing_bid_ask else "INTRADAY_SNAPSHOT"
    for item in normalized:
        item["total_volume_lots"] = total_volume
        item["data_quality"] = summary_quality
    summary = {
        "code": code,
        "trade_date": trade_date,
        "total_volume": total_volume,
        "bid_volume": total_bid,
        "ask_volume": total_ask,
        "neutral_volume": max(total_volume - total_bid - total_ask, 0),
        "data_quality": summary_quality,
        "mapping_note": FUGLE_BID_ASK_MAPPING_NOTE,
        "updated_at": iso_now_text(),
        "fetched_at": fetched_at,
    }
    return normalized, summary


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def table_counts(conn: sqlite3.Connection, codes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for table, code_col, date_col, source_clause in [
        ("fugle_intraday_trades", "code", "trade_date", "source='FUGLE'"),
        ("price_volume_distribution", "stock_id", "trade_date", "source='FUGLE'"),
        ("daily_inner_outer_volume", "stock_code", "trade_date", "source='FUGLE'"),
    ]:
        try:
            rows = []
            for code in codes:
                latest = conn.execute(
                    f"SELECT MAX({date_col}) FROM {table} WHERE {code_col}=? AND {source_clause}",
                    (code,),
                ).fetchone()
                latest_date = latest[0] if latest else None
                count = 0
                if latest_date:
                    count_row = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {code_col}=? AND {date_col}=? AND {source_clause}",
                        (code, latest_date),
                    ).fetchone()
                    count = int(count_row[0] if count_row else 0)
                rows.append({"code": code, "latest_date": latest_date, "row_count": count})
            out[table] = rows
        except sqlite3.Error as exc:
            out[table] = {"error": safe_error(exc)}
    return out


def fetch_existing_trade_rows(conn: sqlite3.Connection, code: str, trade_date: str | None) -> tuple[str | None, list[dict[str, Any]]]:
    ensure_fugle_intraday_schema(conn)
    clean = normalize_code(code)
    if trade_date:
        target_date = trade_date
    else:
        row = conn.execute(
            "SELECT MAX(trade_date) FROM fugle_intraday_trades WHERE code=? AND source='FUGLE'",
            (clean,),
        ).fetchone()
        target_date = row[0] if row else None
    if not target_date:
        return None, []
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT code, trade_date, trade_time, price, size, volume, bid, ask,
                   serial, fetched_at, data_quality, raw_json
            FROM fugle_intraday_trades
            WHERE code=? AND trade_date=? AND source='FUGLE'
            ORDER BY trade_time, serial
            """,
            (clean, target_date),
        ).fetchall()
    ]
    for idx, row in enumerate(rows):
        row["_api_index"] = idx
        row["_source_serial_present"] = bool(row.get("serial"))
        try:
            row["raw_json"] = json.loads(row.get("raw_json") or "{}")
        except Exception:
            row["raw_json"] = {}
    return target_date, rows


def fetch_backfill_targets(conn: sqlite3.Connection, codes: list[str], trade_date: str | None) -> list[tuple[str, str]]:
    ensure_fugle_intraday_schema(conn)
    where = [
        "source='FUGLE'",
        "(side_inferred IS NULL OR TRIM(COALESCE(side_inferred, ''))='' OR side_method IS NULL OR TRIM(COALESCE(side_method, ''))='')",
    ]
    params: list[Any] = []
    if trade_date:
        where.append("trade_date=?")
        params.append(trade_date)
    if codes:
        placeholders = ",".join("?" for _ in codes)
        where.append(f"code IN ({placeholders})")
        params.extend(codes)
    rows = conn.execute(
        f"""
        SELECT DISTINCT code, trade_date
        FROM fugle_intraday_trades
        WHERE {" AND ".join(where)}
        ORDER BY trade_date, code
        """,
        tuple(params),
    ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def update_trade_side_columns(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    normalized = normalize_side_payload(row)
    result = conn.execute(
        """
        UPDATE fugle_intraday_trades
        SET side_inferred=?,
            side_label_zh=?,
            side_method=?,
            side_confidence=?,
            side_reason=?,
            prev_price=?,
            prev_price_source=?
        WHERE code=? AND trade_date=? AND serial=? AND source='FUGLE'
        """,
        (
            normalized.get("side_inferred"),
            normalized.get("side_label_zh"),
            normalized.get("side_method"),
            normalized.get("side_confidence"),
            normalized.get("side_reason"),
            normalized.get("prev_price"),
            normalized.get("prev_price_source"),
            normalized.get("code"),
            normalized.get("trade_date"),
            normalized.get("serial"),
        ),
    )
    return result.rowcount > 0


def run_backfill(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(args.date)):
        return 1, {"ok": False, "reason": "date_must_be_yyyy_mm_dd", "writes_db": False, "requested_date": args.date}
    codes = normalize_codes(args.codes) if args.codes else []
    write = bool(args.write)
    result: dict[str, Any] = {
        "ok": True,
        "mode": "backfill_side_inferred",
        "dry_run": not write,
        "write": write,
        "writes_db": write,
        "codes": codes,
        "requested_date": args.date,
        "stock_results": {},
        "target_pairs": [],
        "schema_added_columns": {},
        "limitations": [
            "Backfill reads existing Fugle trade rows only; it does not call the Fugle API.",
            "Backfill updates only side inference columns.",
            "Backfill dry-run only, add --write to update DB.",
        ],
    }
    with db() as conn:
        result["schema_added_columns"] = ensure_fugle_intraday_schema(conn)
        target_pairs = fetch_backfill_targets(conn, codes, args.date)
        result["target_pairs"] = [{"code": code, "trade_date": trade_date} for code, trade_date in target_pairs]
        target_codes: list[str] = []
        for code, trade_date in target_pairs:
            if code not in target_codes:
                target_codes.append(code)
            _, existing_rows = fetch_existing_trade_rows(conn, code, trade_date)
            previous_close = previous_close_for_trade_date(conn, code, trade_date) if trade_date else None
            inferred_rows, stats = apply_side_inference(existing_rows, previous_close)
            written = 0
            if write:
                for row in inferred_rows:
                    if update_trade_side_columns(conn, row):
                        written += 1
            result["stock_results"][f"{code}:{trade_date}"] = {
                "code": code,
                "trade_date": trade_date,
                "trades": {
                    "written_rows": written,
                    "planned_update_rows": len(inferred_rows),
                    "normalized_rows": len(existing_rows),
                    "data_quality": "BACKFILLED" if written else ("DRY_RUN" if not write and inferred_rows else "NO_TARGET_ROWS"),
                    "side_stats": stats,
                },
            }
        if write:
            conn.commit()
        result["table_counts"] = table_counts(conn, target_codes or codes or DEFAULT_CODES)
    result["success_count"] = sum(
        1
        for stock in result["stock_results"].values()
        if (stock.get("trades") or {}).get("written_rows") or ((stock.get("trades") or {}).get("planned_update_rows") and not write)
    )
    result["planned_update_rows"] = sum(int((stock.get("trades") or {}).get("planned_update_rows") or 0) for stock in result["stock_results"].values())
    result["written_rows"] = sum(int((stock.get("trades") or {}).get("written_rows") or 0) for stock in result["stock_results"].values())
    result["partial"] = result["success_count"] != len(result["stock_results"])
    if result["planned_update_rows"] <= 0:
        result["ok"] = False
        result["reason"] = "no_null_side_rows_found"
    return 0, result


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.backfill_side_inferred:
        return run_backfill(args)
    if args.dry_run and args.write:
        return 1, {"ok": False, "reason": "dry_run_and_write_are_mutually_exclusive", "writes_db": False}

    dry_run = bool(args.dry_run or not args.write)
    codes = normalize_codes(args.codes)
    preflight = fugle_market_preflight(
        requested_date=args.date,
        evidence_codes=normalize_optional_codes(getattr(args, "evidence_codes", None)),
        evidence_max_age_seconds=int(getattr(args, "evidence_max_age_seconds", 300)),
        write_calendar_cache=not dry_run,
        window_start=str(getattr(args, "window_start", "09:00")),
        window_end=str(getattr(args, "window_end", "13:30")),
        capture_phase=str(getattr(args, "capture_phase", "intraday")),
    )
    if not preflight.get("fugle_api_allowed"):
        clean_skip = bool(preflight.get("clean_skip"))
        return (0 if clean_skip else SOURCE_DELAYED_RETRYABLE), {
            "ok": bool(clean_skip),
            "status": preflight.get("status"),
            "reason": preflight.get("reason"),
            "mode": "dry_run" if dry_run else "write",
            "dry_run": dry_run,
            "write": bool(args.write),
            "writes_db": False,
            "codes": codes,
            "today": preflight.get("today"),
            "preflight": preflight,
            "stock_results": {},
            "prune": {"skipped": True, "reason": "market_preflight_blocked"},
            "table_counts": {},
        }
    api_key, key_variants, key_source = load_api_key()
    if not api_key or not key_variants:
        return 1, {
            "ok": False,
            "reason": "missing_fugle_api_key",
            "writes_db": False,
            "full_api_key_displayed": False,
            "api_key_source": key_source,
        }
    today = now_tpe().strftime("%Y-%m-%d")
    target_date = str(args.date or preflight.get("expected_date") or today)
    request_deadline = capture_window_deadline(
        str(getattr(args, "window_end", "13:30"))
    )
    endpoints = ["trades", "volumes"] + (["quote"] if args.include_quote else [])
    fetched_at = time.time()
    rate_limiter = RollingRequestLimiter(
        int(getattr(args, "requests_per_minute", DEFAULT_REQUESTS_PER_MINUTE))
    )
    request_max_retries = int(getattr(args, "request_max_retries", DEFAULT_REQUEST_MAX_RETRIES))
    request_retry_wait_seconds = float(
        getattr(args, "request_retry_wait_seconds", DEFAULT_REQUEST_RETRY_WAIT_SECONDS)
    )
    result: dict[str, Any] = {
        "ok": True,
        "mode": "dry_run" if dry_run else "write",
        "dry_run": dry_run,
        "write": bool(args.write),
        "writes_db": False,
        "codes": codes,
        "today": today,
        "requested_date": args.date,
        "target_date": target_date,
        "capture_phase": str(getattr(args, "capture_phase", "intraday")),
        "preflight": preflight,
        "api_base": args.api_base,
        "has_fugle_api_key": True,
        "api_key_source": key_source,
        "api_key_masked": mask_key(api_key),
        "full_api_key_displayed": False,
        "endpoint_results": {},
        "stock_results": {},
        "schema_added_columns": {},
        "prune": {"skipped": bool(args.skip_prune)},
        "table_counts": {},
        "limitations": [
            "This job is limited to explicit codes only; it is not a full-market update.",
            "volumeAtBid is source-calculated inner volume and volumeAtAsk is source-calculated outer volume; they are not institutional net buy/sell.",
            "Official history_price retention remains separate at 600 trading days.",
        ],
        "capture_deadline": request_deadline.isoformat(timespec="seconds"),
        "window_expired_remaining_codes": [],
    }

    if args.write:
        with db() as conn:
            result["schema_added_columns"] = ensure_fugle_intraday_schema(conn)
            conn.commit()

    window_expired_remaining_codes: list[str] = []
    for code_index, code in enumerate(codes):
        if capture_window_expired(request_deadline):
            window_expired_remaining_codes = list(codes[code_index:])
            break
        stock: dict[str, Any] = {
            "code": code,
            "trades": {"http_status": None, "response_date": None, "data_length": 0, "written_rows": 0, "data_quality": "FAILED", "accepted_for_write": False},
            "volumes": {"http_status": None, "response_date": None, "data_length": 0, "written_rows": 0, "data_quality": "FAILED", "accepted_for_write": False},
            "bid_ask_summary": {"written_rows": 0, "data_quality": "FAILED"},
            "errors": [],
        }
        result["stock_results"][code] = stock
        for endpoint in endpoints:
            if endpoint == "trades":
                response = fetch_trades_paginated(
                    api_base=args.api_base,
                    code=code,
                    key_variants=key_variants,
                    timeout=float(args.timeout),
                    page_limit=int(getattr(args, "trade_page_limit", 500)),
                    max_pages=int(getattr(args, "trade_max_pages", 200)),
                    page_sleep_seconds=float(getattr(args, "trade_page_sleep_seconds", 0.0)),
                    rate_limiter=rate_limiter,
                    max_retries=request_max_retries,
                    retry_wait_seconds=request_retry_wait_seconds,
                    deadline=request_deadline,
                )
            else:
                response = fetch_endpoint(
                    api_base=args.api_base,
                    endpoint=endpoint,
                    code=code,
                    key_variants=key_variants,
                    timeout=float(args.timeout),
                    rate_limiter=rate_limiter,
                    max_retries=request_max_retries,
                    retry_wait_seconds=request_retry_wait_seconds,
                    deadline=request_deadline,
                )
            if str(response.get("error_type") or "") == "CAPTURE_WINDOW_EXPIRED":
                window_expired_remaining_codes = list(codes[code_index:])
                stock[endpoint].update(
                    {
                        "data_quality": "SOURCE_DELAYED",
                        "accepted_for_write": False,
                        "reason": str(response.get("error") or "capture window ended"),
                    }
                )
                stock["errors"].append(
                    {"endpoint": endpoint, "error": str(response.get("error") or "capture window ended")}
                )
                break
            payload = response.get("payload")
            if payload is not None and args.save_json_dir:
                save_json(Path(args.save_json_dir) / f"{target_date}_{code}_{endpoint}.json", payload)
            rows = list(response.get("rows") or []) if endpoint == "trades" else payload_rows(payload)
            response_date = str(response.get("response_date") or payload_date(payload))
            quality, accepted, reason = endpoint_quality(
                rows,
                response_date,
                target_date,
                today,
                bool(args.allow_noncurrent_date or str(getattr(args, "capture_phase", "")) == "latest_completed"),
            )
            if not response.get("ok") or not response.get("json_ok"):
                quality = "FAILED"
                accepted = False
                reason = str(response.get("error") or response.get("json_parse_error") or "request failed")
            summary = {
                "http_status": response.get("http_status"),
                "json_ok": bool(response.get("json_ok")),
                "tls_verify_mode": response.get("tls_verify_mode"),
                "response_date": response_date,
                "response_symbol": payload_symbol(payload),
                "data_length": len(rows),
                "data_quality": quality,
                "accepted_for_write": accepted,
                "reason": reason,
                "url": response.get("url"),
            }
            if endpoint == "trades":
                summary["capture_complete"] = bool(response.get("complete"))
                summary["page_count"] = int(response.get("page_count") or 0)
            result["endpoint_results"].setdefault(code, {})[endpoint] = summary
            if endpoint in {"trades", "volumes"}:
                stock[endpoint].update(summary)
            if response.get("error"):
                stock["errors"].append({"endpoint": endpoint, "error": sanitize_error(response.get("error"), key_variants)})

            if args.write and endpoint == "trades" and not accepted:
                failure_reason = "; ".join(
                    part
                    for part in (
                        f"http_status={response.get('http_status')}" if response.get("http_status") is not None else "",
                        str(response.get("error_type") or ""),
                        sanitize_error(reason, key_variants),
                    )
                    if part
                )[:500]
                with db() as conn:
                    ensure_fugle_intraday_schema(conn)
                    upsert_fugle_capture_run(
                        conn,
                        {
                            "code": code,
                            "trade_date": target_date,
                            "endpoint": "trades",
                            "snapshot_time": iso_now_text(),
                            "page_count": response.get("page_count"),
                            "provider_row_count": len(rows),
                            "normalized_row_count": 0,
                            "stored_row_count": 0,
                            "capture_complete": False,
                            "data_quality": "SOURCE_DELAYED" if quality == "SOURCE_DELAYED" else "UNAVAILABLE",
                            "reason": failure_reason or "provider returned no accepted same-date trades",
                            "fetched_at": fetched_at,
                        },
                        ensure_schema=False,
                    )
                    conn.commit()

            if endpoint == "trades":
                trade_rows = normalize_trade_rows(code, response_date, rows, quality, fetched_at) if accepted else []
                previous_close = None
                if accepted:
                    with db() as conn:
                        previous_close = previous_close_for_trade_date(conn, code, response_date)
                    trade_rows, side_stats = apply_side_inference(trade_rows, previous_close)
                else:
                    side_stats = side_stats_for_trade_rows([], ordering_note="ordering_unverified", previous_close=None)
                if args.write and accepted:
                    with db() as conn:
                        ensure_fugle_intraday_schema(conn)
                        conn.execute(
                            "DELETE FROM fugle_intraday_trades WHERE code=? AND trade_date=? AND source='FUGLE'",
                            (code, response_date),
                        )
                        written = sum(
                            1
                            for row in trade_rows
                            if upsert_fugle_trade(conn, row, ensure_schema=False)
                        )
                        trade_times = [
                            str(row.get("trade_time") or "")
                            for row in trade_rows
                            if str(row.get("trade_time") or "")
                        ]
                        sizes = [num_int(row.get("size")) for row in trade_rows]
                        size_complete = bool(sizes and all(value is not None and value >= 0 for value in sizes))
                        captured_volume_lots = sum(int(value or 0) for value in sizes) if size_complete else None
                        latest_trade_time = max(trade_times) if trade_times else ""
                        session_complete = bool(
                            response.get("complete")
                            and written == len(trade_rows)
                            and written > 0
                            and size_complete
                            and latest_trade_time >= "13:30:00"
                        )
                        upsert_fugle_capture_run(
                            conn,
                            {
                                "code": code,
                                "trade_date": response_date,
                                "endpoint": "trades",
                                "snapshot_time": iso_now_text(),
                                "page_count": response.get("page_count"),
                                "provider_row_count": len(rows),
                                "normalized_row_count": len(trade_rows),
                                "stored_row_count": written,
                                "capture_complete": session_complete,
                                "data_quality": "SESSION_COMPLETE" if session_complete else "PAGINATION_COMPLETE_SESSION_UNVERIFIED",
                                "reason": "" if session_complete else "latest normalized trade did not prove the 13:30 close or rows were incomplete",
                                "latest_trade_time": latest_trade_time,
                                "latest_cumulative_volume": max(
                                    (int(value) for value in (num_int(row.get("volume")) for row in trade_rows) if value is not None),
                                    default=None,
                                ),
                                "captured_volume_lots": captured_volume_lots,
                                "fetched_at": fetched_at,
                            },
                            ensure_schema=False,
                        )
                        conn.commit()
                    stock["trades"]["written_rows"] = written
                    stock["trades"]["session_complete"] = session_complete
                    stock["trades"]["captured_volume_lots"] = captured_volume_lots
                else:
                    stock["trades"]["written_rows"] = 0
                stock["trades"]["normalized_rows"] = len(trade_rows)
                stock["trades"]["side_stats"] = side_stats
                if not args.write:
                    trade_times = [str(row.get("trade_time") or "") for row in trade_rows if row.get("trade_time")]
                    sizes = [num_int(row.get("size")) for row in trade_rows]
                    stock["trades"]["session_complete"] = bool(
                        response.get("complete")
                        and trade_rows
                        and all(value is not None and value >= 0 for value in sizes)
                        and max(trade_times, default="") >= "13:30:00"
                    )
                if not trade_rows:
                    stock["trades"].update(
                        {
                            "accepted_for_write": False,
                            "data_quality": "FAILED",
                            "reason": "trade payload contained no valid dated price rows after normalization",
                        }
                    )
                    result["endpoint_results"][code][endpoint].update(stock["trades"])
            elif endpoint == "volumes":
                volume_rows, bid_ask_summary = normalize_volume_rows(code, response_date, rows, quality, fetched_at) if accepted else ([], {})
                if args.write and accepted:
                    with db() as conn:
                        ensure_fugle_intraday_schema(conn)
                        existing_distribution = conn.execute(
                            """
                            SELECT DISTINCT UPPER(COALESCE(source,'')) AS source,
                                            UPPER(COALESCE(data_quality,source_quality,'')) AS quality
                            FROM price_volume_distribution
                            WHERE stock_id=? AND trade_date=?
                            """,
                            (code, response_date),
                        ).fetchall()
                        existing_summary = conn.execute(
                            """
                            SELECT UPPER(COALESCE(source,'')) AS source
                            FROM daily_inner_outer_volume
                            WHERE stock_code=? AND trade_date=?
                            LIMIT 1
                            """,
                            (code, response_date),
                        ).fetchone()
                        foreign_source = any(str(row["source"] or "") not in {"", "FUGLE"} for row in existing_distribution)
                        foreign_source = foreign_source or bool(
                            existing_summary and str(existing_summary["source"] or "") not in {"", "FUGLE"}
                        )
                        preserved_validated = any(str(row["quality"] or "") == "VALIDATED" for row in existing_distribution)
                        if foreign_source:
                            written = 0
                            summary_written = 0
                            stock["volumes"].update(
                                {
                                    "accepted_for_write": False,
                                    "data_quality": "SOURCE_MISMATCH",
                                    "reason": "same-day rows already belong to another source",
                                }
                            )
                        elif preserved_validated:
                            written = 0
                            summary_written = 0
                            stock["volumes"]["preserved_validated"] = True
                        else:
                            conn.execute(
                                "DELETE FROM price_volume_distribution WHERE stock_id=? AND trade_date=?",
                                (code, response_date),
                            )
                            written = sum(
                                1
                                for row in volume_rows
                                if upsert_fugle_price_volume(conn, row, ensure_schema=False)
                            )
                            summary_written = 1 if upsert_fugle_bid_ask_summary(conn, bid_ask_summary) else 0
                        conn.commit()
                    stock["volumes"]["written_rows"] = written
                    stock["bid_ask_summary"]["written_rows"] = summary_written
                else:
                    stock["volumes"]["written_rows"] = 0
                    stock["bid_ask_summary"]["written_rows"] = 0
                stock["volumes"]["normalized_rows"] = len(volume_rows)
                stock["bid_ask_summary"].update(
                    {
                        "data_quality": bid_ask_summary.get("data_quality") if bid_ask_summary else quality,
                        "total_volume": bid_ask_summary.get("total_volume") if bid_ask_summary else 0,
                        "bid_volume": bid_ask_summary.get("bid_volume") if bid_ask_summary else 0,
                        "ask_volume": bid_ask_summary.get("ask_volume") if bid_ask_summary else 0,
                        "neutral_volume": bid_ask_summary.get("neutral_volume") if bid_ask_summary else 0,
                        "mapping_note": FUGLE_BID_ASK_MAPPING_NOTE,
                    }
                )
            time.sleep(max(0.0, float(args.sleep_seconds)))
        if window_expired_remaining_codes:
            break

    result["window_expired_remaining_codes"] = window_expired_remaining_codes

    complete_codes: list[str] = []
    reconciliation_pending_codes: list[str] = []
    source_delayed_codes: list[str] = []
    retryable_failure_codes: list[str] = []
    capture_phase = str(getattr(args, "capture_phase", "intraday"))
    for code, stock in result.get("stock_results", {}).items():
        trades = stock.get("trades") or {}
        volumes = stock.get("volumes") or {}
        normalized_trade_rows = int(trades.get("normalized_rows") or 0)
        persisted_trade_rows_complete = bool(
            not args.write
            or int(trades.get("written_rows") or 0) == normalized_trade_rows
        )
        terminal_capture = bool(
            trades.get("accepted_for_write")
            and trades.get("capture_complete")
            and normalized_trade_rows > 0
            and persisted_trade_rows_complete
            and volumes.get("accepted_for_write")
            and int(volumes.get("normalized_rows") or 0) > 0
        )
        if terminal_capture and trades.get("session_complete"):
            outcome = "complete"
            complete_codes.append(str(code))
        elif terminal_capture and capture_phase in {"post_close", "latest_completed"}:
            # An illiquid stock does not need a trade stamped exactly 13:30.
            # Keep the persisted capture unverified until the official EOD
            # volume reconciliation promotes it; the network capture itself
            # completed successfully and must not be re-downloaded as a failure.
            outcome = "awaiting_official_volume_reconciliation"
            reconciliation_pending_codes.append(str(code))
        else:
            qualities = {
                str(summary.get("data_quality") or "").upper()
                for summary in (result.get("endpoint_results", {}).get(code, {}) or {}).values()
                if isinstance(summary, dict)
            }
            if "SOURCE_DELAYED" in qualities:
                outcome = "source_delayed"
                source_delayed_codes.append(str(code))
            else:
                outcome = "retryable_failure"
                retryable_failure_codes.append(str(code))
        stock["capture_outcome"] = outcome

    if window_expired_remaining_codes:
        retryable_failure_codes = [
            code for code in retryable_failure_codes
            if code not in window_expired_remaining_codes
        ]
        for code in window_expired_remaining_codes:
            if code not in source_delayed_codes:
                source_delayed_codes.append(code)

    capture_success_count = len(complete_codes) + len(reconciliation_pending_codes)
    result["success_count"] = len(complete_codes)
    result["complete_count"] = len(complete_codes)
    result["complete_codes"] = complete_codes
    result["capture_success_count"] = capture_success_count
    result["reconciliation_pending_count"] = len(reconciliation_pending_codes)
    result["reconciliation_pending_codes"] = reconciliation_pending_codes
    result["source_delayed_count"] = len(source_delayed_codes)
    result["source_delayed_codes"] = source_delayed_codes
    result["retryable_failure_count"] = len(retryable_failure_codes)
    result["retryable_failure_codes"] = retryable_failure_codes
    result["partial"] = bool(source_delayed_codes or retryable_failure_codes)
    result["writes_db"] = bool(
        args.write
        and any(
            int((stock.get("trades") or {}).get("written_rows") or 0) > 0
            or int((stock.get("volumes") or {}).get("written_rows") or 0) > 0
            or int((stock.get("bid_ask_summary") or {}).get("written_rows") or 0) > 0
            for stock in result.get("stock_results", {}).values()
        )
    )
    result["ok"] = not result["partial"]
    result["status"] = (
        "source_delayed"
        if source_delayed_codes
        else "partial"
        if retryable_failure_codes
        else "awaiting_official_volume_reconciliation"
        if reconciliation_pending_codes
        else "ok"
    )

    result["request_throttle"] = rate_limiter.summary()
    if args.write and not args.skip_prune:
        with db() as conn:
            result["prune"] = prune_fugle_intraday_data(conn, retain_days=int(args.prune_retain_days))
    if args.write:
        with db() as conn:
            result["table_counts"] = table_counts(conn, codes)
    if result["ok"]:
        return 0, result
    return (
        SOURCE_DELAYED_RETRYABLE
        if source_delayed_codes
        else PARTIAL_RETRYABLE
    ), result


def yn(value: Any) -> str:
    return "是" if value else "否"


def make_report(result: dict[str, Any]) -> str:
    preflight = result.get("preflight") or {}
    calendar = preflight.get("calendar") or {}
    mis_evidence = preflight.get("mis_evidence") or {}
    request_throttle = result.get("request_throttle") or {}
    api_key_status = yn(result.get("has_fugle_api_key")) if "has_fugle_api_key" in result else "未檢查（市場閘門先阻擋）"
    lines = [
        "# Fugle Intraday Supplemental Update Report",
        "",
        "## 結果摘要",
        "",
        f"- 是否有 FUGLE_API_KEY：{api_key_status}",
        "- 是否顯示完整 API key：否",
        f"- API key 狀態：{result.get('api_key_masked') or 'not configured'}",
        f"- 是否 dry-run：{yn(result.get('dry_run'))}",
        f"- 是否 --write：{yn(result.get('write'))}",
        f"- 是否寫 DB：{yn(result.get('writes_db'))}",
        f"- 模式：{result.get('mode') or '--'}",
        f"- 執行狀態：{result.get('status') or '--'}",
        f"- 嚴格完整股票數：{result.get('complete_count', 0)}",
        f"- 擷取成功股票數（含待官方量核對）：{result.get('capture_success_count', 0)}",
        f"- 待官方量核對股票數：{result.get('reconciliation_pending_count', 0)}",
        f"- 來源延遲股票數：{result.get('source_delayed_count', 0)}",
        f"- 真正可重試失敗股票數：{result.get('retryable_failure_count', 0)}",
        f"- 日曆閘門：{calendar.get('date') or '--'} / {calendar.get('source') or '--'} / verified={yn(calendar.get('verified'))}",
        f"- MIS 本交易時段證據：{mis_evidence.get('status') or ('not_called' if preflight else '--')}",
        f"- Fugle API 是否獲准：{yn(preflight.get('fugle_api_allowed'))}",
        f"- 閘門原因：{preflight.get('reason') or result.get('reason') or '--'}",
        f"- HTTP 請求數：{request_throttle.get('request_count', 0)}",
        f"- 每分鐘請求上限：{request_throttle.get('requests_per_minute', '--')}",
        f"- 主動限速等待次數：{request_throttle.get('throttle_wait_count', 0)}",
        f"- 主動限速等待秒數：{request_throttle.get('throttle_sleep_seconds', 0)}",
        "- backfill dry-run only, add --write to update DB" if result.get("mode") == "backfill_side_inferred" and result.get("dry_run") else "",
        f"- 測試 / 匯入股票：{', '.join(result.get('codes') or [])}",
        f"- response date 檢查基準：{result.get('today') or '--'}",
        f"- 是否執行 prune：{yn(not (result.get('prune') or {}).get('skipped'))}",
        "",
        "本階段只處理指定股票的 Fugle intraday 補充資料，不是全市場更新，也沒有修改 GET API、前端或分析公式。",
        "逐筆 side_inferred 是推論值：先用 bid/ask 判斷，沒有可用 bid/ask 才用前一筆成交價 tick rule。它不是交易所原始內外盤欄位。",
        "",
        "## 各股票結果",
        "",
    ]
    for code, stock in (result.get("stock_results") or {}).items():
        trades = stock.get("trades") or {}
        volumes = stock.get("volumes") or {}
        summary = stock.get("bid_ask_summary") or {}
        side_stats = trades.get("side_stats") or {}
        lines.extend(
            [
                f"### {code}",
                "",
                f"- capture outcome：{stock.get('capture_outcome') or '--'}",
                f"- trades HTTP status：{trades.get('http_status')}",
                f"- trades response date：{trades.get('response_date') or stock.get('trade_date') or '--'}",
                f"- trades data length：{trades.get('data_length') or 0}",
                f"- trades normalized rows：{trades.get('normalized_rows') or 0}",
                f"- trades written rows：{trades.get('written_rows') or 0}",
                f"- trades data_quality：{trades.get('data_quality') or '--'}",
                f"- earliest_trade_time：{side_stats.get('earliest_trade_time') or '--'}",
                f"- latest_trade_time：{side_stats.get('latest_trade_time') or '--'}",
                f"- trade_count：{side_stats.get('trade_count') or 0}",
                f"- ordering_note：{side_stats.get('ordering_note') or '--'}",
                f"- coverage_note：{side_stats.get('coverage_note') or '--'}",
                f"- previous_close：{side_stats.get('previous_close') if side_stats.get('previous_close') is not None else '--'}",
                f"- side_counts：`{json.dumps(side_stats.get('side_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
                f"- method_counts：`{json.dumps(side_stats.get('method_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
                f"- volumes HTTP status：{volumes.get('http_status')}",
                f"- volumes response date：{volumes.get('response_date') or '--'}",
                f"- volumes data length：{volumes.get('data_length') or 0}",
                f"- volumes normalized rows：{volumes.get('normalized_rows') or 0}",
                f"- volumes written rows：{volumes.get('written_rows') or 0}",
                f"- volumes data_quality：{volumes.get('data_quality') or '--'}",
                f"- bid/ask summary written rows：{summary.get('written_rows') or 0}",
                f"- bid_volume：{summary.get('bid_volume') or 0}",
                f"- ask_volume：{summary.get('ask_volume') or 0}",
                f"- neutral_volume：{summary.get('neutral_volume') or 0}",
                "",
            ]
        )
        if stock.get("errors"):
            lines.append("- errors：")
            for error in stock["errors"]:
                lines.append(f"  - {error.get('endpoint')}: {error.get('error')}")
            lines.append("")
    lines.extend(
        [
            "## 欄位 mapping",
            "",
            "- trades：`fugle_intraday_trades.code / trade_date / trade_time / price / size / volume / bid / ask / serial / source / fetched_at / data_quality / raw_json`",
            "- side inference：`side_inferred / side_label_zh / side_method / side_confidence / side_reason / prev_price / prev_price_source`",
            "- volumes：`price_volume_distribution.stock_id / trade_date / price / volume_lots / volume_at_bid / volume_at_ask / neutral_volume_lots / source / data_quality / fetched_at`",
            "- bid/ask summary：`daily_inner_outer_volume.stock_code / trade_date / bid_volume / ask_volume / neutral_volume / total_volume / source / data_quality / mapping_note`",
            "",
            "## side inference 規則",
            "",
            "- 優先使用成交價與 bid/ask：`price >= ask` 為 ASK / 外盤，`price <= bid` 為 BID / 內盤，中間價為 MID / 中性。",
            "- bid/ask 缺漏時才用 tick fallback；第一筆使用本地 `history_price` 的前一交易日收盤價，後續使用前一筆成交價。",
            "- 同價時沿用前一個已知 ASK/BID/MID；第一筆等於前收且無前一方向時為 MID / 中性。",
            "- bid >= ask 時標記 INVALID_QUOTE / 報價異常，不用 tick fallback 掩蓋異常報價。",
            "- 沒有前收時第一筆標記 UNKNOWN / 無法判斷，後續仍可用前一筆成交價分類。",
            "- 不宣稱這是交易所原始內外盤，不做盤前集合競價特殊規則。",
            f"- trade side note：{FUGLE_TRADE_SIDE_NOTE}",
            "",
            "## bid/ask mapping 注意事項",
            "",
            "- 本階段保留 Fugle `volumeAtBid` / `volumeAtAsk` 原始來源欄位。",
            "- bid/ask volume 不強制命名為內盤或外盤；中文 UI 命名前仍需確認 Fugle 欄位語意。",
            f"- mapping note：{FUGLE_BID_ASK_MAPPING_NOTE}",
            "",
            "## 保留規則",
            "",
            "- Fugle intraday 補充資料保留最近 300 個交易日。",
            "- `history_price` 官方日線仍維持既有 600 個交易日保留規則。",
            "- 本階段 prune 僅作用於 Fugle 補充資料，不影響官方日線、法人、TDCC 或籌碼資料。",
            "",
            "## Prune 結果",
            "",
            "```json",
            json.dumps(result.get("prune") or {}, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "## 寫入後表格統計",
            "",
            "```json",
            json.dumps(result.get("table_counts") or {}, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "## 下一步建議",
            "",
            "- 目前建議先維持 2317 / 3491 / 2382 小範圍驗證。",
            "- 若連續數個交易日穩定，再另開任務評估擴大到自選股。",
            "- 不建議直接擴大到全市場 Fugle intraday 抓取。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Update Fugle intraday supplemental trades, volumes, and bid/ask volume summary.")
    parser.add_argument("--codes", default=None)
    parser.add_argument("--date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--output", default="docs/FUGLE_INTRADAY_UPDATE_REPORT.md")
    parser.add_argument("--save-json-dir", default="logs/fugle_intraday_update_json")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--trade-page-limit", type=int, default=500)
    parser.add_argument("--trade-max-pages", type=int, default=200)
    parser.add_argument("--trade-page-sleep-seconds", type=float, default=0.0)
    parser.add_argument("--requests-per-minute", type=int, default=DEFAULT_REQUESTS_PER_MINUTE)
    parser.add_argument("--request-max-retries", type=int, default=DEFAULT_REQUEST_MAX_RETRIES)
    parser.add_argument("--request-retry-wait-seconds", type=float, default=DEFAULT_REQUEST_RETRY_WAIT_SECONDS)
    parser.add_argument("--max-preview", type=int, default=10)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--prune-retain-days", type=int, default=300)
    parser.add_argument("--skip-prune", action="store_true")
    parser.add_argument("--allow-noncurrent-date", action="store_true")
    parser.add_argument("--include-quote", action="store_true")
    parser.add_argument(
        "--evidence-codes",
        default="0050,2330",
        help="Liquid TWSE symbols used only for the read-only current-session MIS gate.",
    )
    parser.add_argument(
        "--evidence-max-age-seconds",
        type=int,
        default=300,
        help="Maximum age of the latest explicit MIS d+t trade evidence.",
    )
    parser.add_argument("--window-start", default="09:00", help="Earliest Taipei time that may call Fugle (HH:MM).")
    parser.add_argument("--window-end", default="13:30", help="Latest Taipei time that may call Fugle (HH:MM).")
    parser.add_argument(
        "--capture-phase",
        choices=["intraday", "post_close", "latest_completed"],
        default="intraday",
        help="Intraday requires live MIS evidence; post_close runs only after 13:30 and awaits official EOD reconciliation.",
    )
    parser.add_argument(
        "--backfill-side-inferred",
        action="store_true",
        help="Backfill side inference columns from existing Fugle trade rows without calling Fugle API; dry-run unless --write is present.",
    )
    args = parser.parse_args()

    code, result = run(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(make_report(result), encoding="utf-8")
    print(f"Report written: {output}")
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in (
                    "ok", "status", "mode", "dry_run", "write", "writes_db",
                    "target_date", "complete_count", "capture_success_count",
                    "reconciliation_pending_count", "reconciliation_pending_codes",
                    "source_delayed_count", "source_delayed_codes",
                    "retryable_failure_count", "retryable_failure_codes", "codes",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
