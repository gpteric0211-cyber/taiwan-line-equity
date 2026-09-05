from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

from core.http import request_json

try:
    import truststore  # type: ignore
except Exception:
    truststore = None
else:
    try:
        truststore.inject_into_ssl()
    except Exception:
        pass


TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TWSE_LENDING_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U"
TPEX_MARGIN_URL = (
    "https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/"
    "margin_bal_result.php"
)
TPEX_LENDING_URL = (
    "https://www.tpex.org.tw/web/stock/margin_trading/margin_sbl/"
    "margin_sbl_result.php"
)
TWSE_MIN_REQUEST_INTERVAL_SECONDS = 0.75
_twse_request_lock = threading.Lock()
_twse_last_request_started = 0.0


def _integer(value: Any) -> int | None:
    text = str(value or "").replace(",", "").strip()
    if text in {"", "--", "-"}:
        return 0
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _stock_code(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}", text) else None


def _date_text(value: Any) -> str | None:
    text = re.sub(r"\D", "", str(value or ""))
    if len(text) == 8 and text.startswith("20"):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) == 7:
        return f"{int(text[:3]) + 1911:04d}-{text[3:5]}-{text[5:7]}"
    return None


def _roc_date(value: str) -> str:
    year, month, day = (int(part) for part in value.split("-"))
    return f"{year - 1911:03d}/{month:02d}/{day:02d}"


def _percentage(value: Any) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if text in {"", "--", "-"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _utilization(balance: int | None, limit: int | None) -> float | None:
    if balance is None or limit is None or limit <= 0:
        return None
    return round(float(balance) / float(limit) * 100.0, 6)


def _fetch(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    http_get: Callable[..., Any] | None = None,
) -> Any:
    if http_get is not None:
        return http_get(url, params=params, timeout=30)
    if url not in {TWSE_MARGIN_URL, TWSE_LENDING_URL}:
        return request_json(url, params=params, timeout=30)

    global _twse_last_request_started
    with _twse_request_lock:
        remaining = (
            TWSE_MIN_REQUEST_INTERVAL_SECONDS
            - (time.monotonic() - _twse_last_request_started)
        )
        if remaining > 0:
            time.sleep(remaining)
        _twse_last_request_started = time.monotonic()
        return request_json(
            url,
            params=params,
            retries=5,
            retry_wait=2,
            timeout=30,
        )


def fetch_twse_margin_balance(
    trade_date: str,
    *,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    payload = _fetch(
        TWSE_MARGIN_URL,
        params={"date": trade_date.replace("-", ""), "selectType": "ALL", "response": "json"},
        http_get=http_get,
    )
    data_date = _date_text(payload.get("date")) if isinstance(payload, dict) else None
    tables = payload.get("tables") if isinstance(payload, dict) else []
    table = next(
        (
            item for item in tables or []
            if isinstance(item, dict) and "融資融券彙總" in str(item.get("title") or "")
        ),
        {},
    )
    items: list[dict[str, Any]] = []
    for raw in table.get("data") or []:
        if not isinstance(raw, list) or len(raw) < 16:
            continue
        code = _stock_code(raw[0])
        values = [_integer(value) for value in raw[2:15]]
        if not code or any(value is None for value in values):
            continue
        items.append({
            "trade_date": data_date,
            "code": code,
            "market": "listed",
            "margin_buy_lots": values[0],
            "margin_sell_lots": values[1],
            "margin_cash_repayment_lots": values[2],
            "margin_prev_balance_lots": values[3],
            "margin_balance_lots": values[4],
            "margin_utilization_pct": _utilization(values[4], values[5]),
            "margin_utilization_method": "derived_official_balance_limit",
            "margin_limit_lots": values[5],
            "short_buy_lots": values[6],
            "short_sell_lots": values[7],
            "short_stock_repayment_lots": values[8],
            "short_prev_balance_lots": values[9],
            "short_balance_lots": values[10],
            "short_utilization_pct": _utilization(values[10], values[11]),
            "short_utilization_method": "derived_official_balance_limit",
            "short_limit_lots": values[11],
            "source": "TWSE_MI_MARGN",
        })
    return {
        "ok": bool(items) and str(payload.get("stat") or "").upper() == "OK",
        "source": "TWSE_MI_MARGN",
        "data_date": data_date,
        "items": items,
        "row_count": len(items),
    }


def fetch_twse_lending_balance(
    trade_date: str,
    *,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    payload = _fetch(
        TWSE_LENDING_URL,
        params={"date": trade_date.replace("-", ""), "selectType": "ALL", "response": "json"},
        http_get=http_get,
    )
    data_date = _date_text(payload.get("date")) if isinstance(payload, dict) else None
    items: list[dict[str, Any]] = []
    for raw in (payload.get("data") if isinstance(payload, dict) else []) or []:
        if not isinstance(raw, list) or len(raw) < 15:
            continue
        code = _stock_code(raw[0])
        values = [_integer(value) for value in raw[2:14]]
        if not code or any(value is None for value in values):
            continue
        items.append({
            "trade_date": data_date,
            "code": code,
            "market": "listed",
            "sbl_prev_balance_shares": values[6],
            "sbl_sell_shares": values[7],
            "sbl_return_shares": values[8],
            "sbl_adjust_shares": values[9],
            "sbl_balance_shares": values[10],
            "source": "TWSE_TWT93U",
        })
    return {
        "ok": bool(items) and str(payload.get("stat") or "").upper() == "OK",
        "source": "TWSE_TWT93U",
        "data_date": data_date,
        "items": items,
        "row_count": len(items),
    }


def fetch_tpex_margin_balance(
    trade_date: str,
    *,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    payload = _fetch(
        TPEX_MARGIN_URL,
        params={"l": "zh-tw", "o": "json", "d": _roc_date(trade_date), "s": "0,asc"},
        http_get=http_get,
    )
    data_date = _date_text(payload.get("date")) if isinstance(payload, dict) else None
    tables = payload.get("tables") if isinstance(payload, dict) else []
    rows = (tables or [{}])[0].get("data") or []
    items: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, list) or len(raw) < 18:
            continue
        code = _stock_code(raw[0])
        values = [_integer(value) for value in raw[2:18]]
        if not code or any(value is None for index, value in enumerate(values) if index not in {6, 14}):
            continue
        item = {
            "trade_date": data_date,
            "code": code,
            "market": "otc",
            "margin_prev_balance_lots": values[0],
            "margin_buy_lots": values[1],
            "margin_sell_lots": values[2],
            "margin_cash_repayment_lots": values[3],
            "margin_balance_lots": values[4],
            "margin_utilization_pct": _percentage(raw[8]),
            "margin_utilization_method": "source_reported",
            "margin_limit_lots": values[7],
            "short_prev_balance_lots": values[8],
            "short_sell_lots": values[9],
            "short_buy_lots": values[10],
            "short_stock_repayment_lots": values[11],
            "short_balance_lots": values[12],
            "short_utilization_pct": _percentage(raw[16]),
            "short_utilization_method": "source_reported",
            "short_limit_lots": values[15],
            "source": "TPEX_MARGIN_BAL_RESULT",
        }
        if item["trade_date"] == data_date:
            items.append(item)
    return {
        "ok": bool(items) and str(payload.get("stat") or "").upper() == "OK",
        "source": "TPEX_MARGIN_BAL_RESULT",
        "data_date": data_date,
        "requested_date": trade_date,
        "items": items,
        "row_count": len(items),
    }


def fetch_tpex_lending_balance(
    trade_date: str,
    *,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    payload = _fetch(
        TPEX_LENDING_URL,
        params={"l": "zh-tw", "o": "json", "d": _roc_date(trade_date), "s": "0,asc,0"},
        http_get=http_get,
    )
    data_date = _date_text(payload.get("date")) if isinstance(payload, dict) else None
    tables = payload.get("tables") if isinstance(payload, dict) else []
    rows = (tables or [{}])[0].get("data") or []
    items: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, list) or len(raw) < 13:
            continue
        code = _stock_code(raw[0])
        values = [_integer(value) for value in raw[8:13]]
        if not code or any(value is None for value in values):
            continue
        item = {
            "trade_date": data_date,
            "code": code,
            "market": "otc",
            "sbl_prev_balance_shares": values[0],
            "sbl_sell_shares": values[1],
            "sbl_return_shares": values[2],
            "sbl_adjust_shares": values[3],
            "sbl_balance_shares": values[4],
            "source": "TPEX_MARGIN_SBL_RESULT",
        }
        if item["trade_date"] == data_date:
            items.append(item)
    return {
        "ok": bool(items) and str(payload.get("stat") or "").upper() == "OK",
        "source": "TPEX_MARGIN_SBL_RESULT",
        "data_date": data_date,
        "requested_date": trade_date,
        "items": items,
        "row_count": len(items),
    }
