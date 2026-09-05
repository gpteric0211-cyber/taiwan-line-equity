from __future__ import annotations

import logging
import re
import time
from typing import Any

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        logging.getLogger(__name__).exception("Failed to inject truststore for TWSE valuation adapter")

from core.config import (
    HEADERS,
    TWSE_BWIBBU_ALL,
    TWSE_BWIBBU_D_OPENAPI,
    TWSE_BWIBBU_D_RWD,
)
from core.http import request_json
from core.utils import normalize_date, now_tpe


logger = logging.getLogger(__name__)


class TwseValuationSourceDelayed(RuntimeError):
    """The requested official valuation date has not been published yet."""

    def __init__(
        self,
        requested_date: str,
        *,
        available_dates: set[str] | None = None,
        source_errors: list[str] | None = None,
    ) -> None:
        dates = sorted({value for value in (available_dates or set()) if value})
        self.requested_date = requested_date
        self.available_dates = dates
        self.available_date = dates[-1] if dates else None
        self.source_errors = list(source_errors or [])
        available = self.available_date or "unknown"
        super().__init__(
            f"TWSE BWIBBU source delayed: requested {requested_date}, latest available {available}"
        )


def normalize_symbol(symbol: Any) -> str:
    """Normalize Taiwan stock symbols to four numeric digits when possible."""
    s = str(symbol or "").strip().upper()
    s = re.sub(r"\.(TW|TWO)$", "", s)
    s = re.sub(r"\D", "", s)
    return s.zfill(4) if s else ""


def normalize_twse_valuation_number(value: Any) -> float | None:
    """Normalize TWSE valuation numeric fields without inventing missing values."""
    if value is None:
        return None
    s = str(value).replace(",", "").replace("%", "").strip()
    if s in {"", "-", "--", "N/A", "NA", "null", "None"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _get_any(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _rwd_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    fields = payload.get("fields") or payload.get("titles") or []
    data = payload.get("data") or []
    rows: list[dict[str, Any]] = []
    if isinstance(fields, list) and isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                rows.append(item)
            elif isinstance(item, list):
                rows.append({str(fields[idx]): value for idx, value in enumerate(item) if idx < len(fields)})
    return rows


def parse_twse_bwibbu_rows(raw_rows: Any, data_date: str | None = None) -> list[dict[str, Any]]:
    """Parse TWSE BWIBBU OpenAPI/RWD rows into the project's valuation shape."""
    if isinstance(raw_rows, dict):
        rows = _rwd_rows(raw_rows)
    elif isinstance(raw_rows, list):
        rows = [row for row in raw_rows if isinstance(row, dict)]
    else:
        rows = []

    parsed: list[dict[str, Any]] = []
    observed_at = now_tpe()
    updated_at = observed_at.strftime("%Y-%m-%d %H:%M:%S")
    available_at = observed_at.isoformat(timespec="seconds")
    fallback_date = normalize_date(data_date) or observed_at.date().isoformat()
    for row in rows:
        symbol = normalize_symbol(_get_any(row, ["Code", "證券代號", "有價證券代號", "stock_id", "symbol"]))
        if not re.fullmatch(r"\d{4}", symbol):
            continue
        row_date = normalize_date(_get_any(row, ["Date", "日期", "data_date"])) or fallback_date
        parsed.append(
            {
                "data_date": row_date,
                "symbol": symbol,
                "name": _get_any(row, ["Name", "證券名稱", "有價證券名稱", "stock_name", "name"]),
                "close_price": normalize_twse_valuation_number(
                    _get_any(row, ["ClosePrice", "ClosingPrice", "收盤價", "close_price", "Close"])
                ),
                "dividend_yield": normalize_twse_valuation_number(
                    _get_any(row, ["DividendYield", "殖利率(%)", "殖利率", "dividend_yield"])
                ),
                "dividend_year": _get_any(row, ["DividendYear", "股利年度", "dividend_year"]),
                "pe_ratio": normalize_twse_valuation_number(
                    _get_any(row, ["PEratio", "本益比", "pe_ratio", "PER"])
                ),
                "pb_ratio": normalize_twse_valuation_number(
                    _get_any(row, ["PBratio", "股價淨值比", "pb_ratio", "PBR"])
                ),
                "financial_year_quarter": _get_any(
                    row,
                    ["FiscalYearQuarter", "財報年/季", "財報年季", "financial_year_quarter"],
                ),
                "source": "TWSE_BWIBBU",
                "source_status": "ok",
                "updated_at": updated_at,
                "available_at": available_at,
                "timezone": "Asia/Taipei",
            }
        )
    return parsed


def _fetch_openapi(data_date: str | None) -> list[dict[str, Any]]:
    payload = request_json(TWSE_BWIBBU_D_OPENAPI, retries=2, retry_wait=3, timeout=25)
    rows = parse_twse_bwibbu_rows(payload, data_date)
    if rows:
        return rows
    payload = request_json(TWSE_BWIBBU_ALL, retries=2, retry_wait=3, timeout=25)
    return parse_twse_bwibbu_rows(payload, data_date)


def _fetch_rwd(data_date: str | None) -> list[dict[str, Any]]:
    normalized = normalize_date(data_date)
    params: dict[str, Any] = {"response": "json", "selectType": "ALL"}
    if normalized:
        params["date"] = normalized.replace("-", "")
    payload = request_json(
        TWSE_BWIBBU_D_RWD,
        params=params,
        headers={**HEADERS, "Referer": "https://www.twse.com.tw/"},
        retries=2,
        retry_wait=3,
        timeout=25,
    )
    if isinstance(payload, dict):
        status = str(payload.get("stat") or "").strip().upper()
        response_date = normalize_date(payload.get("date"))
        if status and status != "OK":
            raise RuntimeError(f"TWSE BWIBBU RWD status is {status}")
        if normalized and response_date != normalized:
            raise RuntimeError(
                f"TWSE BWIBBU RWD returned {response_date or 'unknown date'} for requested {normalized}"
            )
    return parse_twse_bwibbu_rows(payload, data_date)


def fetch_twse_bwibbu_day(data_date: str | None = None) -> list[dict[str, Any]]:
    """Fetch official TWSE BWIBBU valuation rows."""
    started = time.perf_counter()
    errors: list[str] = []
    available_dates: set[str] = set()
    requested_date = normalize_date(data_date)
    sources = (
        [
            ("TWSE RWD BWIBBU_d", _fetch_rwd),
            ("TWSE OpenAPI BWIBBU_d/BWIBBU_ALL", _fetch_openapi),
        ]
        if requested_date
        else [
            ("TWSE OpenAPI BWIBBU_d/BWIBBU_ALL", _fetch_openapi),
            ("TWSE RWD BWIBBU_d", _fetch_rwd),
        ]
    )
    for source_name, fetcher in sources:
        try:
            rows = fetcher(data_date)
            logger.info(
                "TWSE valuation fetch source=%s data_date=%s rows=%s duration=%.3f",
                source_name,
                data_date,
                len(rows),
                time.perf_counter() - started,
            )
            row_dates = {
                normalize_date(row.get("data_date"))
                for row in rows
                if row.get("data_date")
            }
            row_dates.discard(None)
            if rows and requested_date and row_dates != {requested_date}:
                available_dates.update(str(value) for value in row_dates if value)
                returned = ",".join(sorted(row_dates)) or "unknown"
                errors.append(
                    f"{source_name}: returned {returned} for requested {requested_date}"
                )
                continue
            if rows:
                return rows
            errors.append(f"{source_name}: empty")
        except Exception as exc:
            logger.info("TWSE valuation source unavailable source=%s reason=%s", source_name, exc)
            errors.append(f"{source_name}: {exc}")
    if (
        requested_date
        and available_dates
        and max(available_dates) < requested_date
    ):
        raise TwseValuationSourceDelayed(
            requested_date,
            available_dates=available_dates,
            source_errors=errors,
        )
    raise RuntimeError("; ".join(errors) or "TWSE BWIBBU returned no rows")


def fetch_twse_valuation_by_symbol(symbol: str, data_date: str | None = None) -> dict[str, Any]:
    """Fetch one stock's official TWSE BWIBBU valuation without crashing callers."""
    code = normalize_symbol(symbol)
    try:
        for row in fetch_twse_bwibbu_day(data_date):
            if row.get("symbol") == code:
                return row
        return {
            "symbol": code,
            "source": "TWSE_BWIBBU",
            "source_status": "not_found",
            "pe_ratio": None,
            "pb_ratio": None,
            "dividend_yield": None,
            "reason": "TWSE BWIBBU 無該股票估值資料",
        }
    except Exception as exc:
        return {
            "symbol": code,
            "source": "TWSE_BWIBBU",
            "source_status": "fetch_failed",
            "pe_ratio": None,
            "pb_ratio": None,
            "dividend_yield": None,
            "reason": str(exc),
        }
