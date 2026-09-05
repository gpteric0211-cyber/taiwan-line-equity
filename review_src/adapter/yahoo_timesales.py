from __future__ import annotations

from collections import defaultdict
from typing import Any

from core.utils import parse_num


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "B", "買", "外盤"}:
        return "BUY"
    if text in {"SELL", "S", "賣", "內盤"}:
        return "SELL"
    return "UNKNOWN"


def _volume_lots(row: dict[str, Any]) -> int:
    for key in ("volume_lots", "lots", "volume", "qty", "quantity"):
        if key in row:
            value = parse_num(row.get(key))
            if value is None:
                return 0
            return max(0, int(round(float(value))))
    return 0


def build_price_volume_distribution_from_time_sales(
    rows: list[dict[str, Any]],
    *,
    code: str | None = None,
    trade_date: str | None = None,
    fetched_at: float | None = None,
) -> list[dict[str, Any]]:
    """Aggregate supplemental time-sales rows by price.

    This function does not infer buy/sell with tick rules. Only explicit BUY
    and SELL sides are assigned to buy/sell buckets; blank or unknown side is
    accumulated into neutral volume.
    """
    buckets: dict[float, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        price = parse_num(row.get("price"))
        if price is None:
            continue
        price_f = float(price)
        lots = _volume_lots(row)
        bucket = buckets.setdefault(
            price_f,
            {
                "code": str(code or row.get("code") or row.get("stock_id") or "").strip(),
                "trade_date": str(trade_date or row.get("trade_date") or row.get("date") or "").strip(),
                "price": price_f,
                "volume_lots": 0,
                "buy_volume_lots": 0,
                "sell_volume_lots": 0,
                "neutral_volume_lots": 0,
                "source": "YAHOO",
                "source_quality": "SCRAPED",
                "fetched_at": fetched_at,
            },
        )
        bucket["volume_lots"] += lots
        side = _side(row.get("side"))
        if side == "BUY":
            bucket["buy_volume_lots"] += lots
        elif side == "SELL":
            bucket["sell_volume_lots"] += lots
        else:
            bucket["neutral_volume_lots"] += lots
    return [buckets[p] for p in sorted(buckets)]


def summarize_side_quality(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = defaultdict(int)
    for row in rows or []:
        if not isinstance(row, dict):
            counts["invalid"] += 1
            continue
        counts[_side(row.get("side")).lower()] += 1
    return dict(counts)
