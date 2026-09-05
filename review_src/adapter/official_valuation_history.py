from __future__ import annotations

"""Official exact-date TWSE/TPEx valuation history adapters."""

import json
import math
import threading
import time
from datetime import date
from typing import Any

import requests

from adapter.twse_valuation import parse_twse_bwibbu_rows
from core.config import HEADERS, TWSE_BWIBBU_D_RWD, safe_error
from core.utils import normalize_date, now_tpe

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass


TPEX_DAILY_PE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate"
TPEX_DAILY_PE_REFERER = "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/daily-pe.html"
TWSE_MIN_REQUEST_INTERVAL_SECONDS = 5.0
TWSE_BURST_COOLDOWN_SECONDS = 65.0
TWSE_EMPTY_RESPONSE_COOLDOWN_SECONDS = 600.0
TWSE_MAX_ATTEMPTS = 5
TPEX_MAX_ATTEMPTS = 4
_twse_request_lock = threading.Lock()
_twse_last_request_started = 0.0
_twse_blocked_until = 0.0


def _extend_twse_cooldown(seconds: float) -> None:
    """Age every TWSE worker together after an official throttle response."""

    global _twse_blocked_until
    with _twse_request_lock:
        _twse_blocked_until = max(
            _twse_blocked_until,
            time.monotonic() + max(float(seconds), 0.0),
        )


def _number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if text in {"", "-", "--", "N/A", "NA"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_tpex_daily_pe_payload(
    payload: Any,
    *,
    requested_date: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    tables = payload.get("tables") if isinstance(payload, dict) else None
    table = tables[0] if isinstance(tables, list) and tables else {}
    values = table.get("data") if isinstance(table, dict) else None
    rows: list[dict[str, Any]] = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, list) or len(raw) < 8:
            continue
        symbol = str(raw[0] or "").strip()
        if len(symbol) != 4 or not symbol.isdigit():
            continue
        rows.append(
            {
                "data_date": requested_date,
                "symbol": symbol,
                "name": str(raw[1] or "").strip() or None,
                "close_price": None,
                "dividend_yield": _number(raw[5]),
                "dividend_year": str(raw[4] or "").strip() or None,
                "pe_ratio": _number(raw[2]),
                "pb_ratio": _number(raw[6]),
                "financial_year_quarter": str(raw[7] or "").strip() or None,
                "source": "TPEX_PERATIO_ANALYSIS",
                "source_status": "ok",
                "updated_at": observed_at[:19].replace("T", " "),
                "available_at": observed_at,
                "timezone": "Asia/Taipei",
            }
        )
    return rows


def fetch_tpex_daily_valuation(
    data_date: str,
    *,
    timeout: float = 30,
    max_attempts: int = TPEX_MAX_ATTEMPTS,
) -> dict[str, Any]:
    normalized_date = date.fromisoformat(str(data_date)).isoformat()
    observed_at = now_tpe().isoformat(timespec="seconds")
    attempts = max(1, min(int(max_attempts), TPEX_MAX_ATTEMPTS))
    last_error: Exception | None = None
    rows: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                TPEX_DAILY_PE_URL,
                params={"date": normalized_date.replace("-", "/"), "cate": ""},
                headers={**HEADERS, "Referer": TPEX_DAILY_PE_REFERER},
                timeout=timeout,
            )
            response.raise_for_status()
            rows = parse_tpex_daily_pe_payload(
                response.json(),
                requested_date=normalized_date,
                observed_at=observed_at,
            )
            if not rows:
                raise RuntimeError(
                    f"TPEx exact-date valuation returned no rows for {normalized_date}"
                )
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = status_code in {429, 500, 502, 503, 504} or isinstance(
                exc,
                (
                    json.JSONDecodeError,
                    requests.exceptions.JSONDecodeError,
                    requests.ConnectionError,
                    requests.Timeout,
                ),
            )
            if not retryable or attempt >= attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    if last_error is not None:
        return {
            "ok": False,
            "status": "source_delayed",
            "data_date": normalized_date,
            "rows": [],
            "error": safe_error(last_error),
            "attempts": attempts,
        }
    return {
        "ok": bool(rows),
        "status": "ok" if rows else "source_delayed",
        "data_date": normalized_date,
        "rows": rows,
        "row_count": len(rows),
    }


def _throttled_twse_get(*, normalized_date: str, timeout: float) -> requests.Response:
    """Serialize the official exact-date endpoint and honor its burst limit."""

    global _twse_blocked_until, _twse_last_request_started
    with _twse_request_lock:
        now = time.monotonic()
        remaining = max(
            TWSE_MIN_REQUEST_INTERVAL_SECONDS - (now - _twse_last_request_started),
            _twse_blocked_until - now,
        )
        if remaining > 0:
            time.sleep(remaining)
        _twse_last_request_started = time.monotonic()
        response = requests.get(
            TWSE_BWIBBU_D_RWD,
            params={
                "response": "json",
                "selectType": "ALL",
                "date": normalized_date.replace("-", ""),
            },
            headers={**HEADERS, "Referer": "https://www.twse.com.tw/"},
            timeout=timeout,
        )
        status_code = int(getattr(response, "status_code", 200) or 0)
        content = bytes(getattr(response, "content", b"") or b"").lstrip()
        if status_code in {428, 429}:
            _twse_blocked_until = (
                time.monotonic() + TWSE_BURST_COOLDOWN_SECONDS
            )
        elif status_code == 307 or (
            status_code == 200 and not content.startswith((b"{", b"["))
        ):
            # TWSE's edge occasionally reports throttling as a 307 HTML page
            # or a 200 response with an empty/non-JSON body.  Treat both as a
            # shared cooldown signal instead of burning every queued date.
            _twse_blocked_until = (
                time.monotonic() + TWSE_EMPTY_RESPONSE_COOLDOWN_SECONDS
            )
        return response


def fetch_twse_daily_valuation(
    data_date: str,
    *,
    timeout: float = 30,
    max_attempts: int = TWSE_MAX_ATTEMPTS,
) -> dict[str, Any]:
    normalized_date = date.fromisoformat(str(data_date)).isoformat()
    attempts = max(1, min(int(max_attempts), TWSE_MAX_ATTEMPTS))
    last_error: Exception | None = None
    rows: list[dict[str, Any]] = []
    for attempt in range(1, attempts + 1):
        try:
            response = _throttled_twse_get(
                normalized_date=normalized_date,
                timeout=timeout,
            )
            if int(getattr(response, "status_code", 200) or 0) != 200:
                raise requests.HTTPError(
                    f"TWSE BWIBBU HTTP {getattr(response, 'status_code', 'unknown')}",
                    response=response,
                )
            response.raise_for_status()
            payload = response.json()
            status = str(payload.get("stat") or "").strip().upper()
            response_date = normalize_date(payload.get("date"))
            if status and status != "OK":
                raise RuntimeError(f"TWSE BWIBBU RWD status is {status}")
            if response_date != normalized_date:
                raise RuntimeError(
                    "TWSE BWIBBU exact-date mismatch: "
                    f"requested {normalized_date}, received {response_date or 'unknown'}"
                )
            rows = parse_twse_bwibbu_rows(payload, normalized_date)
            if rows and {str(row.get("data_date")) for row in rows} != {normalized_date}:
                raise RuntimeError("TWSE BWIBBU row dates do not match requested date")
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            invalid_json = isinstance(
                exc,
                (json.JSONDecodeError, requests.exceptions.JSONDecodeError),
            )
            if invalid_json:
                _extend_twse_cooldown(TWSE_EMPTY_RESPONSE_COOLDOWN_SECONDS)
            retryable = status_code in {307, 428, 429, 500, 502, 503, 504} or isinstance(
                exc,
                (
                    json.JSONDecodeError,
                    requests.exceptions.JSONDecodeError,
                    requests.ConnectionError,
                    requests.Timeout,
                ),
            )
            if not retryable or attempt >= attempts:
                break
            time.sleep(min(2 ** (attempt - 1), 8))
    if last_error is not None:
        return {
            "ok": False,
            "status": "source_delayed",
            "data_date": normalized_date,
            "rows": [],
            "error": safe_error(last_error),
            "attempts": attempts,
        }
    return {
        "ok": bool(rows),
        "status": "ok" if rows else "source_delayed",
        "data_date": normalized_date,
        "rows": rows,
        "row_count": len(rows),
    }
