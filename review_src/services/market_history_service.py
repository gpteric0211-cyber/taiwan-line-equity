from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Mapping

from core.data_quality import assess_daily_ohlcv
from core.market_foundation_schema import source_rank
from core.utils import normalize_date, today_iso


OFFICIAL_SOURCE_MIN_RANK = 100
TRUSTED_SOURCE_QUALITIES = {"OFFICIAL", "OK", "HIGH", "VALIDATED"}


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _display_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _market_for_row(row: Mapping[str, Any]) -> str | None:
    market = str(row.get("market") or "").strip().lower()
    if market in {"listed", "twse"}:
        return "listed"
    if market in {"otc", "tpex"}:
        return "otc"
    source = str(row.get("source") or "").upper()
    if "TPEX" in source:
        return "otc"
    if "TWSE" in source:
        return "listed"
    return None


def _source_label(markets: set[str]) -> str:
    if markets == {"listed"}:
        return "臺灣證券交易所官方每日行情"
    if markets == {"otc"}:
        return "證券櫃檯買賣中心官方每日行情"
    return "臺灣證券交易所／證券櫃檯買賣中心官方每日行情"


def build_official_kline_payload(
    code: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    limit: int = 120,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Build a chart payload from persisted official, validated daily bars only.

    OHLCV values are never inferred from the candlestick geometry.  The chart
    receives the original persisted exchange values after source, unit, date,
    and OHLCV consistency gates have passed.
    """

    normalized_code = str(code or "").strip().zfill(4)[:4]
    max_rows = max(1, min(int(limit), 260))
    cutoff = normalize_date(as_of_date) or today_iso()
    rejection_counts: Counter[str] = Counter()
    qualified_by_date: dict[str, dict[str, Any]] = {}

    for raw in rows:
        row = dict(raw)
        row_code = str(row.get("code") or "").strip().zfill(4)
        if row_code != normalized_code:
            rejection_counts["stock_code_mismatch"] += 1
            continue
        if source_rank(row.get("source")) < OFFICIAL_SOURCE_MIN_RANK:
            rejection_counts["non_official_source"] += 1
            continue
        quality = str(row.get("source_quality") or "").strip().upper()
        if quality not in TRUSTED_SOURCE_QUALITIES:
            rejection_counts["untrusted_source_quality"] += 1
            continue

        volume = _finite_number(row.get("volume"))
        volume_unit = str(row.get("volume_unit") or "").strip().lower()
        if volume_unit == "lots" and volume is not None:
            volume *= 1000
        elif volume_unit != "shares":
            rejection_counts["unknown_volume_unit"] += 1
            continue
        row["volume"] = volume
        row["volume_unit"] = "shares"

        validation = assess_daily_ohlcv(row)
        if not validation.get("ready"):
            rejection_counts["invalid_ohlcv"] += 1
            continue
        trade_date = str(validation.get("date") or "")
        if trade_date > cutoff:
            rejection_counts["future_trade_date"] += 1
            continue
        if trade_date in qualified_by_date:
            rejection_counts["duplicate_trade_date"] += 1
            continue

        open_price = float(row["open"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        close_price = float(row["close"])
        volume_shares = float(row["volume"])
        qualified_by_date[trade_date] = {
            "trade_date": trade_date,
            "open": _display_number(open_price),
            "high": _display_number(high_price),
            "low": _display_number(low_price),
            "close": _display_number(close_price),
            "volume_shares": _display_number(volume_shares),
            "market": _market_for_row(row),
        }

    qualified = [qualified_by_date[key] for key in sorted(qualified_by_date)]
    truncated_row_count = max(len(qualified) - max_rows, 0)
    items = qualified[-max_rows:]
    if not items:
        return {
            "available": False,
            "status": "unavailable",
            "reason": "no_valid_official_ohlcv",
            "message": "沒有通過交易所來源與 OHLCV 品質檢查的歷史日線。",
            "code": normalized_code,
            "date_order": "ascending",
            "items": [],
            "summary": None,
            "data_quality": {
                "official_only": True,
                "estimated": False,
                "qualified_row_count": 0,
                "rejected_row_count": sum(rejection_counts.values()),
                "rejection_counts": dict(rejection_counts),
                "truncated_row_count": 0,
            },
        }

    period_high = max(float(item["high"]) for item in items)
    period_low = min(float(item["low"]) for item in items)
    total_volume = sum(float(item["volume_shares"]) for item in items)
    latest = items[-1]
    markets = {str(item["market"]) for item in items if item.get("market")}
    return {
        "available": True,
        "status": "ok",
        "reason": "validated_official_ohlcv",
        "message": "僅呈現已通過品質檢查的交易所官方日線；未使用推估值。",
        "code": normalized_code,
        "date_order": "ascending",
        "source_label": _source_label(markets),
        "first_trade_date": items[0]["trade_date"],
        "latest_trade_date": latest["trade_date"],
        "items": items,
        "summary": {
            "bar_count": len(items),
            "latest_close": latest["close"],
            "period_high": _display_number(period_high),
            "period_low": _display_number(period_low),
            "total_volume_shares": _display_number(total_volume),
            "average_volume_shares": _display_number(total_volume / len(items)),
        },
        "data_quality": {
            "status": "ok",
            "official_only": True,
            "estimated": False,
            "volume_unit": "shares",
            "quality_gate": "official_source_and_valid_ohlcv",
            "qualified_row_count": len(qualified),
            "displayed_row_count": len(items),
            "rejected_row_count": sum(rejection_counts.values()),
            "rejection_counts": dict(rejection_counts),
            "truncated_row_count": truncated_row_count,
        },
    }
