from __future__ import annotations

import re
from typing import Any

from adapter import tpex as _tpex_tls  # noqa: F401
from core.config import HEADERS, safe_error
from core.data_quality import assess_daily_ohlcv
from core.http import request_json
from core.utils import normalize_date, parse_num


TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"


def _normalized_fields(fields: list[Any]) -> dict[str, int]:
    return {re.sub(r"\s+", "", str(value)): index for index, value in enumerate(fields)}


def _index(indexes: dict[str, int], *names: str) -> int | None:
    for name in names:
        key = re.sub(r"\s+", "", name)
        if key in indexes:
            return indexes[key]
    return None


def _table_with_fields(payload: dict[str, Any], required: set[str]) -> dict[str, Any] | None:
    for table in payload.get("tables") or []:
        if not isinstance(table, dict):
            continue
        fields = _normalized_fields(table.get("fields") or [])
        if required.issubset(fields):
            return table
    return None


def _no_trade_row(code: str, trade_date: str, market: str, source: str, row: Any) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "code": code,
        "market": market,
        "reason": "official_zero_volume_or_missing_price_row",
        "source": source,
        "source_url": TWSE_MI_INDEX_URL if market == "listed" else TPEX_DAILY_QUOTES_URL,
        "source_quality": "official",
        "evidence": {"row": row},
    }


def fetch_twse_full_market_date(trade_date: str) -> dict[str, Any]:
    requested = normalize_date(trade_date)
    out: dict[str, Any] = {
        "ok": False,
        "market": "listed",
        "source": "TWSE MI_INDEX",
        "requested_date": requested,
        "data_date": None,
        "rows": [],
        "verified_no_trade_dates": [],
        "error": None,
    }
    if not requested:
        out["error"] = "invalid trade date"
        return out
    try:
        payload = request_json(
            TWSE_MI_INDEX_URL,
            params={"date": requested.replace("-", ""), "type": "ALLBUT0999", "response": "json"},
            headers={**HEADERS, "Referer": "https://www.twse.com.tw/"},
            retries=3,
            retry_wait=1.0,
            timeout=35,
        )
        data_date = normalize_date(payload.get("date")) if isinstance(payload, dict) else None
        if str(payload.get("stat") or "").upper() != "OK" or data_date != requested:
            raise RuntimeError(f"TWSE MI_INDEX date/status mismatch: {data_date} {payload.get('stat')}")
        table = _table_with_fields(
            payload,
            {"證券代號", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"},
        )
        if not table:
            raise RuntimeError("TWSE MI_INDEX stock table schema mismatch")
        fields = _normalized_fields(table.get("fields") or [])
        keys = {
            "code": _index(fields, "證券代號"),
            "name": _index(fields, "證券名稱"),
            "volume": _index(fields, "成交股數"),
            "amount": _index(fields, "成交金額"),
            "open": _index(fields, "開盤價"),
            "high": _index(fields, "最高價"),
            "low": _index(fields, "最低價"),
            "close": _index(fields, "收盤價"),
        }
        rows: list[dict[str, Any]] = []
        no_trade: list[dict[str, Any]] = []
        for raw in table.get("data") or []:
            if not isinstance(raw, list):
                continue
            code = str(raw[keys["code"]] or "").strip() if keys["code"] is not None else ""
            if not re.fullmatch(r"\d{4}", code):
                continue
            values = {key: parse_num(raw[index]) for key, index in keys.items() if key not in {"code", "name"} and index is not None}
            if values.get("close") is None or values.get("volume") in (None, 0):
                no_trade.append(_no_trade_row(code, requested, "listed", "TWSE MI_INDEX", raw))
                continue
            row = {
                "date": requested,
                "code": code,
                "name": str(raw[keys["name"]] or "").strip() if keys["name"] is not None else "",
                **values,
                "volume_unit": "shares",
                "source": "TWSE MI_INDEX",
                "source_quality": "official",
                "market": "listed",
            }
            if assess_daily_ohlcv(row).get("ready"):
                rows.append(row)
        out.update({"ok": bool(rows or no_trade), "data_date": data_date, "rows": rows, "verified_no_trade_dates": no_trade})
    except Exception as exc:
        out["error"] = safe_error(exc)
    return out


def fetch_tpex_full_market_date(trade_date: str) -> dict[str, Any]:
    requested = normalize_date(trade_date)
    out: dict[str, Any] = {
        "ok": False,
        "market": "otc",
        "source": "TPEX DAILY_QUOTES",
        "requested_date": requested,
        "data_date": None,
        "rows": [],
        "verified_no_trade_dates": [],
        "error": None,
    }
    if not requested:
        out["error"] = "invalid trade date"
        return out
    try:
        payload = request_json(
            TPEX_DAILY_QUOTES_URL,
            params={"date": requested.replace("-", "/"), "id": "", "response": "json"},
            headers={
                **HEADERS,
                "Referer": "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/pricing.html",
                "X-Requested-With": "XMLHttpRequest",
            },
            retries=3,
            retry_wait=1.0,
            timeout=35,
        )
        data_date = normalize_date(payload.get("date")) if isinstance(payload, dict) else None
        if str(payload.get("stat") or "").lower() != "ok" or data_date != requested:
            raise RuntimeError(f"TPEx dailyQuotes date/status mismatch: {data_date} {payload.get('stat')}")
        table = _table_with_fields(
            payload,
            {"代號", "收盤", "開盤", "最高", "最低", "成交股數", "成交金額(元)"},
        )
        if not table:
            raise RuntimeError("TPEx dailyQuotes stock table schema mismatch")
        fields = _normalized_fields(table.get("fields") or [])
        keys = {
            "code": _index(fields, "代號"),
            "name": _index(fields, "名稱"),
            "close": _index(fields, "收盤"),
            "open": _index(fields, "開盤"),
            "high": _index(fields, "最高"),
            "low": _index(fields, "最低"),
            "volume": _index(fields, "成交股數"),
            "amount": _index(fields, "成交金額(元)"),
        }
        rows: list[dict[str, Any]] = []
        no_trade: list[dict[str, Any]] = []
        for raw in table.get("data") or []:
            if not isinstance(raw, list):
                continue
            code = str(raw[keys["code"]] or "").strip() if keys["code"] is not None else ""
            if not re.fullmatch(r"\d{4}", code):
                continue
            values = {key: parse_num(raw[index]) for key, index in keys.items() if key not in {"code", "name"} and index is not None}
            if values.get("close") is None or values.get("volume") in (None, 0):
                no_trade.append(_no_trade_row(code, requested, "otc", "TPEX DAILY_QUOTES", raw))
                continue
            row = {
                "date": requested,
                "code": code,
                "name": str(raw[keys["name"]] or "").strip() if keys["name"] is not None else "",
                **values,
                "volume_unit": "shares",
                "source": "TPEX DAILY_QUOTES",
                "source_quality": "official",
                "market": "otc",
            }
            if assess_daily_ohlcv(row).get("ready"):
                rows.append(row)
        out.update({"ok": bool(rows or no_trade), "data_date": data_date, "rows": rows, "verified_no_trade_dates": no_trade})
    except Exception as exc:
        out["error"] = safe_error(exc)
    return out
