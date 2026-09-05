from __future__ import annotations

import re
import time
from datetime import date
from typing import Any, Callable

import requests

# Importing the official TPEx adapter installs the project's truststore hook
# before this endpoint is contacted on packaged Windows runtimes.
from adapter import tpex as _tpex_tls  # noqa: F401
from core.config import HEADERS, mask_secret_text, safe_error
from core.data_quality import assess_daily_ohlcv
from core.utils import normalize_date, parse_num


TPEX_TRADING_STOCK_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
TPEX_TRADING_STOCK_REFERER = (
    "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/stock-pricing.html"
)


def _response_json(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    status_code = int(getattr(response, "status_code", 200) or 200)
    if status_code >= 400:
        body = str(getattr(response, "text", "") or "").strip()[:240]
        raise RuntimeError(f"HTTP {status_code}: {mask_secret_text(body)}")
    if hasattr(response, "encoding"):
        response.encoding = "utf-8"
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("TPEx tradingStock returned a non-object payload")
    return payload


def _post_json(
    data: dict[str, str],
    *,
    http_post: Callable[..., Any] | None,
    retries: int,
    timeout: float,
) -> dict[str, Any]:
    requester = http_post or requests.post
    last: Exception | None = None
    headers = {
        **HEADERS,
        "Referer": TPEX_TRADING_STOCK_REFERER,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    for attempt in range(max(int(retries), 1)):
        try:
            payload = _response_json(
                requester(
                    TPEX_TRADING_STOCK_URL,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            )
            if str(payload.get("stat") or "").lower() != "ok":
                raise RuntimeError(str(payload.get("stat") or "TPEx tradingStock source error"))
            return payload
        except Exception as exc:
            last = exc
            if attempt < max(int(retries), 1) - 1:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(safe_error(last))


def _field_indexes(fields: list[Any]) -> dict[str, int]:
    normalized = {re.sub(r"\s+", "", str(value)): idx for idx, value in enumerate(fields)}
    aliases = {
        "date": ("日期",),
        # The current official report calls this field 成交仟股.  Older
        # responses/tests used 成交張數; both represent thousands of shares.
        "volume_lots": ("成交仟股", "成交千股", "成交張數"),
        "amount_thousands": ("成交仟元", "成交千元"),
        "open": ("開盤",),
        "high": ("最高",),
        "low": ("最低",),
        "close": ("收盤",),
        "transactions": ("筆數", "成交筆數"),
    }
    indexes: dict[str, int] = {}
    for key, names in aliases.items():
        for name in names:
            if name in normalized:
                indexes[key] = normalized[name]
                break
    return indexes


def _calendar_date(value: Any) -> str | None:
    """Normalize ROC/Western dates and reject impossible calendar dates."""

    normalized = normalize_date(value)
    if not normalized:
        return None
    try:
        date.fromisoformat(normalized)
    except ValueError:
        return None
    return normalized


def fetch_tpex_stock_month_rows(
    code: str,
    month_date: str,
    *,
    http_post: Callable[..., Any] | None = None,
    retries: int = 3,
    timeout: float = 20,
) -> dict[str, Any]:
    """Read one month of official TPEx OHLCV without writing the database.

    The official report expresses volume in lots and amount in thousands of
    TWD; both are normalized to shares/TWD.  A row containing ``--`` prices
    and zero volume is returned as explicit no-trade evidence, never as a K
    line and never as a zero-price bar.
    """

    raw_code = str(code or "").strip()
    normalized_code = raw_code.zfill(4)
    target = _calendar_date(month_date)
    if (
        not re.fullmatch(r"\d{1,4}", raw_code)
        or normalized_code == "0000"
    ):
        return {
            "code": normalized_code,
            "month": target[:7] if target else None,
            "ok": False,
            "rows": [],
            "verified_no_trade_dates": [],
            "error": "invalid code",
        }
    if not target:
        return {
            "code": normalized_code,
            "month": None,
            "ok": False,
            "rows": [],
            "verified_no_trade_dates": [],
            "error": "invalid month_date",
        }

    month = target[:7]
    try:
        payload = _post_json(
            {
                "code": normalized_code,
                "date": month.replace("-", "/") + "/01",
                "response": "json",
            },
            http_post=http_post,
            retries=retries,
            timeout=timeout,
        )
    except Exception as exc:
        return {
            "code": normalized_code,
            "month": month,
            "ok": False,
            "rows": [],
            "verified_no_trade_dates": [],
            "source": "TPEX TRADING_STOCK",
            "source_url": TPEX_TRADING_STOCK_URL,
            "error": safe_error(exc),
        }

    tables = payload.get("tables")
    table = tables[0] if isinstance(tables, list) and tables and isinstance(tables[0], dict) else {}
    fields = table.get("fields") if isinstance(table.get("fields"), list) else []
    raw_rows = table.get("data") if isinstance(table.get("data"), list) else []
    indexes = _field_indexes(fields)
    required_fields = {"date", "volume_lots", "amount_thousands", "open", "high", "low", "close"}
    if not required_fields.issubset(indexes):
        return {
            "code": normalized_code,
            "month": month,
            "ok": False,
            "rows": [],
            "verified_no_trade_dates": [],
            "source": "TPEX TRADING_STOCK",
            "source_url": TPEX_TRADING_STOCK_URL,
            "error": "TPEx tradingStock field schema mismatch",
            "fields": fields,
        }

    rows: list[dict[str, Any]] = []
    no_trade: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    max_index = max(indexes.values())
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) <= max_index:
            invalid_rows.append({"reason": "row_shape", "row": raw})
            continue
        trade_date = _calendar_date(raw[indexes["date"]])
        if not trade_date or trade_date[:7] != month:
            invalid_rows.append({"reason": "invalid_or_cross_month_date", "row": raw})
            continue
        if trade_date in seen_dates:
            invalid_rows.append({"reason": "duplicate_trade_date", "row": raw})
            continue
        seen_dates.add(trade_date)
        volume_lots = parse_num(raw[indexes["volume_lots"]])
        amount_thousands = parse_num(raw[indexes["amount_thousands"]])
        raw_price_tokens = {
            key: str(raw[indexes[key]] or "").strip()
            for key in ("open", "high", "low", "close")
        }
        prices = {
            key: parse_num(raw[indexes[key]])
            for key in ("open", "high", "low", "close")
        }
        official_missing_prices = all(
            token in {"--", "---"}
            for token in raw_price_tokens.values()
        )
        transaction_index = indexes.get("transactions")
        transaction_count = (
            parse_num(raw[transaction_index]) if transaction_index is not None else None
        )
        if (
            official_missing_prices
            and volume_lots is not None
            and volume_lots >= 0
            and amount_thousands is not None
            and amount_thousands >= 0
        ):
            if volume_lots == 0 and amount_thousands == 0:
                no_regular_lot_reason = "official_zero_volume_no_price_row"
            elif volume_lots == 0:
                no_regular_lot_reason = "official_no_regular_lot_ohlcv_with_residual_activity"
            else:
                no_regular_lot_reason = "official_no_ohlcv_with_residual_activity"
            no_trade.append({
                "trade_date": trade_date,
                "code": normalized_code,
                "market": "otc",
                "reason": no_regular_lot_reason,
                "source": "TPEX TRADING_STOCK",
                "source_url": TPEX_TRADING_STOCK_URL,
                "source_quality": "official",
                "evidence": {
                    "report_month": month,
                    "fields": fields,
                    "row": raw,
                    "reported_regular_lot_volume": volume_lots,
                    "reported_amount_thousands": amount_thousands,
                    "reported_transaction_count": transaction_count,
                    "has_regular_lot_ohlcv": False,
                },
            })
            continue
        if amount_thousands is None or amount_thousands <= 0:
            invalid_rows.append({"reason": "amount_missing_or_not_positive", "row": raw})
            continue
        row = {
            "date": trade_date,
            "code": normalized_code,
            **prices,
            "volume": volume_lots * 1000 if volume_lots is not None else None,
            "amount": amount_thousands * 1000 if amount_thousands is not None else None,
            "volume_unit": "shares",
            "source": "TPEX TRADING_STOCK",
            "source_quality": "official",
            "market": "otc",
            "reported_volume_lots": volume_lots,
        }
        quality = assess_daily_ohlcv(row)
        if not quality.get("ready"):
            invalid_rows.append({"reason": quality.get("reason"), "row": raw})
            continue
        volume_shares = float(row["volume"])
        amount_twd = float(row["amount"])
        average_trade_price = amount_twd / volume_shares
        # Both 成交仟股 and 成交仟元 are rounded aggregate fields.  Account for
        # up to one reported thousand shares plus one thousand TWD of source
        # precision loss before requiring implied VWAP to stay inside the
        # daily low/high range.
        rounding_tolerance = (
            float(row["high"]) * 1000.0 + 1000.0
        ) / volume_shares
        if not (
            float(row["low"]) - rounding_tolerance
            <= average_trade_price
            <= float(row["high"]) + rounding_tolerance
        ):
            invalid_rows.append({"reason": "amount_volume_price_inconsistent", "row": raw})
            continue
        row["reported_average_price"] = average_trade_price
        rows.append(row)

    rows.sort(key=lambda row: str(row["date"]))
    no_trade.sort(key=lambda row: str(row["trade_date"]))
    has_rows = bool(rows or no_trade)
    # A successful official stock-month response may be empty when the stock
    # had no daily bar for the entire month.  That is a valid source result;
    # callers with an official market calendar may classify the absent dates.
    complete = not invalid_rows
    if invalid_rows:
        error = "TPEx tradingStock contained invalid or conflicting rows"
    elif not has_rows:
        error = None
    else:
        error = None
    return {
        "code": normalized_code,
        "month": month,
        "ok": complete,
        "rows": rows,
        "row_count": len(rows),
        "verified_no_trade_dates": no_trade,
        "verified_no_trade_count": len(no_trade),
        "empty_official_report": not has_rows,
        "invalid_rows": invalid_rows,
        "source": "TPEX TRADING_STOCK",
        "source_url": TPEX_TRADING_STOCK_URL,
        "error": error,
    }
