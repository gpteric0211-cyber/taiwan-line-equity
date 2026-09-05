from __future__ import annotations

"""Bounded Yahoo close-history retrieval for supplemental overseas context.

The returned rows are candidates only.  They keep the retrieval timestamp as
``available_at`` so a historical import cannot pretend that this database saw
the row in the past.
"""

import math
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.config import HEADERS
from core.http import request_json
from core.market_timing import next_taiwan_trading_date
from core.utils import now_tpe, parse_num


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def _finite_positive(value: Any) -> float | None:
    number = parse_num(value)
    if number is None:
        return None
    value_float = float(number)
    return value_float if math.isfinite(value_float) and value_float > 0 else None


def fetch_global_market_history_rows(
    ticker: str,
    *,
    calendar_days: int = 760,
    timeout: float = 30,
) -> dict[str, Any]:
    """Fetch one ticker's daily closes without writing the database."""

    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker is required")
    observed_at = now_tpe()
    start = observed_at - timedelta(days=max(30, int(calendar_days)))
    end = observed_at + timedelta(days=2)
    payload = request_json(
        YAHOO_CHART_URL.format(ticker=normalized_ticker),
        params={
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
            "events": "div,splits,capitalGains",
        },
        headers=HEADERS,
        retries=2,
        retry_wait=1,
        timeout=timeout,
    )
    result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
    meta = result.get("meta") or {}
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [None])[0] or {})
    closes = quote.get("close") or []
    timezone_name = str(meta.get("exchangeTimezoneName") or "America/New_York")
    try:
        exchange_timezone = ZoneInfo(timezone_name)
    except Exception:
        exchange_timezone = ZoneInfo("America/New_York")
        timezone_name = "America/New_York"
    fetched_at = observed_at.isoformat(timespec="seconds")
    currency = str(meta.get("currency") or "USD")
    valid: list[tuple[int, str, float, str]] = []
    incomplete_session_rows_skipped = 0
    observed_exchange_time = observed_at.astimezone(exchange_timezone)
    for index, raw_timestamp in enumerate(timestamps):
        close = _finite_positive(closes[index] if index < len(closes) else None)
        if close is None:
            continue
        try:
            source_time = datetime.fromtimestamp(
                int(raw_timestamp), tz=exchange_timezone
            )
        except (TypeError, ValueError, OSError):
            continue
        # Yahoo may expose the current daily candle while the exchange is
        # still trading.  Its ``close`` is then only the latest intraday
        # price, not an official completed-session close.  Keep a conservative
        # two-hour post-close buffer before accepting a same-date candle.
        source_date = source_time.date()
        session_finalized = source_date < observed_exchange_time.date() or (
            source_date == observed_exchange_time.date()
            and observed_exchange_time.hour >= 18
        )
        if not session_finalized:
            incomplete_session_rows_skipped += 1
            continue
        valid.append(
            (
                int(raw_timestamp),
                source_time.date().isoformat(),
                close,
                source_time.isoformat(timespec="seconds"),
            )
        )
    valid.sort(key=lambda item: item[0])
    rows: list[dict[str, Any]] = []
    previous_close: float | None = None
    for _raw_timestamp, market_date, close, source_timestamp in valid:
        change_pct = (
            (close / previous_close - 1.0) * 100.0
            if previous_close not in (None, 0)
            else None
        )
        rows.append(
            {
                "market_date": market_date,
                "ticker": normalized_ticker,
                "display_name": normalized_ticker,
                "close": close,
                "previous_close": previous_close,
                "change_pct": change_pct,
                "currency": currency,
                "source": "Yahoo Finance chart",
                "source_quality": "supplemental",
                "fetched_at": fetched_at,
                "available_at": fetched_at,
                "market_session": "historical_close_backfill",
                "effective_tw_trade_date": next_taiwan_trading_date(
                    market_date, strictly_after=True
                ),
                "exchange_timezone": timezone_name,
                "source_market_timestamp": source_timestamp,
            }
        )
        previous_close = close
    return {
        "ok": bool(rows),
        "ticker": normalized_ticker,
        "rows": rows,
        "row_count": len(rows),
        "min_date": rows[0]["market_date"] if rows else None,
        "max_date": rows[-1]["market_date"] if rows else None,
        "source_quality": "supplemental",
        "incomplete_session_rows_skipped": incomplete_session_rows_skipped,
    }
