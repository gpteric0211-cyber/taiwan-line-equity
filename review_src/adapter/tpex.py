from __future__ import annotations

import logging
import re
import time
from typing import Any

from core.config import HEADERS, safe_error
from core.date_utils import normalize_date
from core.http import request_json
from core.utils import now_tpe, parse_num

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        logging.getLogger(__name__).exception("Failed to inject truststore for TPEx adapter")

TPEX_STOCK_LIST_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TPEX_DAILY_CLOSE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TPEX_PERATIO_ANALYSIS_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"


def _get_any(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def normalize_tpex_code(value: Any) -> str | None:
    s = str(value or "").strip()
    return s if re.fullmatch(r"\d{4}", s) else None


def normalize_tpex_stock_row(row: dict[str, Any]) -> dict[str, Any] | None:
    code = normalize_tpex_code(_get_any(row, ["SecuritiesCompanyCode", "Code", "公司代號", "有價證券代號", "代號"]))
    if not code:
        return None
    return {
        "code": code,
        "name": str(_get_any(row, ["CompanyName", "SecuritiesCompanyName", "公司名稱", "有價證券名稱", "名稱"]) or "").strip(),
        "listing_date": normalize_date(
            _get_any(row, ["DateOfListing", "ListingDate", "上櫃日期", "掛牌日期"])
        ),
        "data_date": normalize_date(_get_any(row, ["Date", "資料日期", "出表日期"])),
        "paid_in_capital_twd": parse_num(
            _get_any(row, ["Paidin.Capital.NTDollars", "實收資本額"])
        ),
        "issued_shares": parse_num(_get_any(row, ["IssueShares", "已發行普通股數"])),
        "market_type": "otc",
        "exchange": "TPEX",
        "source": "TPEX OpenAPI",
        "raw": row,
    }


def fetch_tpex_stock_list() -> dict[str, Any]:
    out = {"ok": False, "source": "TPEX OpenAPI", "url": TPEX_STOCK_LIST_URL, "rows": 0, "valid_rows": 0, "items": [], "error": None}
    try:
        data = request_json(TPEX_STOCK_LIST_URL, headers=HEADERS, retries=2, retry_wait=1.5, timeout=20)
        rows = data if isinstance(data, list) else []
        items = [item for item in (normalize_tpex_stock_row(r) for r in rows if isinstance(r, dict)) if item]
        out.update({"ok": bool(items), "rows": len(rows), "valid_rows": len(items), "items": items[:], "columns": list(rows[0].keys()) if rows else []})
    except Exception as exc:
        out["error"] = safe_error(exc)
    return out


def normalize_tpex_daily_close_row(row: dict[str, Any]) -> dict[str, Any] | None:
    code = normalize_tpex_code(_get_any(row, ["SecuritiesCompanyCode", "Code", "代號", "有價證券代號"]))
    if not code:
        return None
    close = parse_num(_get_any(row, ["Close", "ClosingPrice", "收盤", "收盤價"]))
    if close is None:
        return None
    return {
        "code": code,
        "name": str(_get_any(row, ["CompanyName", "Name", "名稱", "有價證券名稱"]) or "").strip(),
        "date": normalize_date(_get_any(row, ["Date", "日期", "資料日期"])),
        "open": parse_num(_get_any(row, ["Open", "OpeningPrice", "開盤", "開盤價"])),
        "high": parse_num(_get_any(row, ["High", "HighestPrice", "最高", "最高價"])),
        "low": parse_num(_get_any(row, ["Low", "LowestPrice", "最低", "最低價"])),
        "close": close,
        "volume": parse_num(_get_any(row, ["TradingShares", "TradeVolume", "成交股數", "成交量"])),
        "amount": parse_num(_get_any(row, ["TransactionAmount", "TradeValue", "成交金額"])),
        "source": "TPEX OpenAPI",
        "raw": row,
    }


def fetch_tpex_daily_close_quotes() -> dict[str, Any]:
    out = {"ok": False, "source": "TPEX OpenAPI", "url": TPEX_DAILY_CLOSE_URL, "rows": 0, "valid_rows": 0, "items": [], "error": None}
    try:
        data = request_json(TPEX_DAILY_CLOSE_URL, headers=HEADERS, retries=2, retry_wait=1.5, timeout=20)
        rows = data if isinstance(data, list) else []
        items = [item for item in (normalize_tpex_daily_close_row(r) for r in rows if isinstance(r, dict)) if item]
        dates = [str(item.get("date")) for item in items if item.get("date")]
        out.update({
            "ok": bool(items),
            "rows": len(rows),
            "valid_rows": len(items),
            "items": items[:],
            "columns": list(rows[0].keys()) if rows else [],
            "data_date": max(dates) if dates else None,
        })
    except Exception as exc:
        out["error"] = safe_error(exc)
    return out


def normalize_tpex_valuation_number(value: Any) -> float | None:
    return parse_num(value)


def normalize_tpex_valuation_row(row: dict[str, Any]) -> dict[str, Any] | None:
    code = normalize_tpex_code(_get_any(row, ["SecuritiesCompanyCode", "Code", "有價證券代號", "代號"]))
    if not code:
        return None
    data_date = normalize_date(_get_any(row, ["Date", "資料日期", "日期"]))
    if not data_date:
        return None
    observed_at = now_tpe()
    return {
        "data_date": data_date,
        "symbol": code,
        "name": str(_get_any(row, ["CompanyName", "Name", "公司名稱", "有價證券名稱", "名稱"]) or "").strip(),
        "close_price": None,
        "cash_dividend_per_share": normalize_tpex_valuation_number(_get_any(row, ["DividendPerShare", "CashDividend"])),
        "dividend_yield": normalize_tpex_valuation_number(_get_any(row, ["YieldRatio", "DividendYield", "殖利率", "殖利率(%)"])),
        "dividend_year": None,
        "pe_ratio": normalize_tpex_valuation_number(_get_any(row, ["PriceEarningRatio", "PEratio", "PER", "本益比"])),
        "pb_ratio": normalize_tpex_valuation_number(_get_any(row, ["PriceBookRatio", "PBratio", "PBR", "股價淨值比"])),
        "financial_year_quarter": None,
        "source": "TPEX_PERATIO_ANALYSIS",
        "source_status": "ok",
        "updated_at": observed_at.strftime("%Y-%m-%d %H:%M:%S"),
        "available_at": observed_at.isoformat(timespec="seconds"),
        "timezone": "Asia/Taipei",
        "raw": row,
    }


def parse_tpex_peratio_analysis_rows(raw_rows: Any) -> list[dict[str, Any]]:
    rows = raw_rows if isinstance(raw_rows, list) else []
    parsed = [
        item
        for item in (normalize_tpex_valuation_row(row) for row in rows if isinstance(row, dict))
        if item
    ]
    return parsed


def fetch_tpex_peratio_analysis() -> list[dict[str, Any]]:
    started = time.perf_counter()
    data = request_json(TPEX_PERATIO_ANALYSIS_URL, headers=HEADERS, retries=2, retry_wait=1.5, timeout=25)
    rows = parse_tpex_peratio_analysis_rows(data)
    logging.getLogger(__name__).info(
        "TPEx valuation fetch rows=%s duration=%.3f",
        len(rows),
        time.perf_counter() - started,
    )
    return rows


def fetch_tpex_valuation_by_symbol(symbol: str) -> dict[str, Any]:
    code = normalize_tpex_code(symbol) or str(symbol or "").strip()
    try:
        for row in fetch_tpex_peratio_analysis():
            if row.get("symbol") == code:
                return row
        return {
            "symbol": code,
            "source": "TPEX_PERATIO_ANALYSIS",
            "source_status": "not_found",
            "pe_ratio": None,
            "pb_ratio": None,
            "dividend_yield": None,
            "reason": "TPEx PERatio analysis has no valuation row for this stock.",
        }
    except Exception as exc:
        return {
            "symbol": code,
            "source": "TPEX_PERATIO_ANALYSIS",
            "source_status": "fetch_failed",
            "pe_ratio": None,
            "pb_ratio": None,
            "dividend_yield": None,
            "reason": safe_error(exc),
        }
