from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from adapter.yahoo import fetch_yfinance_quote
from core.db import db
from core.market_timing import availability_contract
from repository.global_market_repository import (
    prune_global_market_rows,
    upsert_global_market_rows,
)


TPE = ZoneInfo("Asia/Taipei")
GLOBAL_MARKET_TICKERS = {
    "^GSPC": "標普500",
    "^IXIC": "那斯達克",
    "^SOX": "費城半導體",
    "TSM": "台積電ADR",
    "XLF": "美國金融類股",
    "XLI": "美國工業類股",
    "XLB": "美國原物料類股",
    "XLE": "美國能源類股",
    "XLV": "美國醫療類股",
    "XLY": "美國非必需消費類股",
    "XLP": "美國必需消費類股",
    "XLU": "美國公用事業類股",
    "XLK": "美國科技類股",
}


def _source_market_timestamp(quote: dict[str, Any]) -> str | None:
    try:
        raw_timestamp = int(quote.get("regular_market_time"))
    except (TypeError, ValueError):
        return None
    timezone_name = str(quote.get("exchange_timezone") or "UTC")
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone = ZoneInfo("UTC")
    return datetime.fromtimestamp(raw_timestamp, tz=timezone).isoformat(timespec="seconds")


def refresh_global_market_snapshot(
    *,
    dry_run: bool = False,
    fetch_quote: Callable[[str], dict[str, Any]] = fetch_yfinance_quote,
) -> dict[str, Any]:
    """Fetch a bounded US close set and persist it through an explicit task."""

    fetched_at = datetime.now(TPE).isoformat(timespec="seconds")
    timing = availability_contract(fetched_at=fetched_at)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for ticker, display_name in GLOBAL_MARKET_TICKERS.items():
        quote = fetch_quote(ticker)
        market_date = str(quote.get("date") or "")[:10]
        try:
            close = float(quote.get("price"))
        except (TypeError, ValueError):
            close = 0.0
        if not quote.get("ok") or not market_date or close <= 0:
            failures.append({"ticker": ticker, "reason": str(quote.get("error") or "quote unavailable")})
            continue
        previous_close = quote.get("previous_close")
        change_pct = quote.get("change_pct")
        rows.append(
            {
                "market_date": market_date,
                "ticker": ticker,
                "display_name": display_name,
                "close": close,
                "previous_close": float(previous_close) if previous_close is not None else None,
                "change_pct": float(change_pct) if change_pct is not None else None,
                "currency": str(quote.get("currency") or "USD"),
                "source": str(quote.get("source") or "Yahoo Finance chart"),
                "source_quality": "supplemental",
                "fetched_at": fetched_at,
                "available_at": timing["available_at"],
                "market_session": timing["market_session"],
                "effective_tw_trade_date": timing["effective_tw_trade_date"],
                "exchange_timezone": str(quote.get("exchange_timezone") or "") or None,
                "source_market_timestamp": _source_market_timestamp(quote),
            }
        )
    written = 0
    pruned = 0
    if rows and not dry_run:
        with closing(db()) as conn:
            written = upsert_global_market_rows(conn, rows)
            pruned = prune_global_market_rows(conn, retain_market_days=400)
            conn.commit()
    status = "ok" if len(rows) == len(GLOBAL_MARKET_TICKERS) else "partial" if rows else "unavailable"
    return {
        "ok": bool(rows),
        "status": status,
        "dry_run": dry_run,
        "rows_fetched": len(rows),
        "rows_written": written,
        "rows_pruned": pruned,
        "market_dates": sorted({row["market_date"] for row in rows}),
        "failures": failures,
        "source_quality": "supplemental",
    }
