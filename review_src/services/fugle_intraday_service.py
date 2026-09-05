from __future__ import annotations

import json
import time
from typing import Any

from adapter.fugle import fetch_fugle_quote_network
from core.config import FUGLE_INTRADAY_MODE, FUGLE_INTRADAY_POLL_SECONDS, FUGLE_MAX_WATCHLIST_SYMBOLS
from core.market_session import market_is_open_now
from core.utils import now_tpe, parse_num
from repository.daily_chip_momentum_repository import cleanup_intraday_1m_history, upsert_intraday_snapshot
from repository.watchlist_repository import get_watchlist_codes


def _parse_fugle_quote_snapshot(code: str, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    price = parse_num(data.get("lastPrice") or data.get("closePrice") or data.get("price") or data.get("last"))
    if price is None:
        return None
    volume = parse_num(data.get("totalVolume") or data.get("volume") or data.get("tradeVolume"))
    turnover = parse_num(data.get("totalAmount") or data.get("turnover") or data.get("tradeValue"))
    return {
        "stock_id": str(code).zfill(4)[:4],
        "quote_time": data.get("date") or data.get("time") or now_tpe().strftime("%Y-%m-%d %H:%M:%S"),
        "price": price,
        "open": parse_num(data.get("openPrice") or data.get("open")),
        "high": parse_num(data.get("highPrice") or data.get("high")),
        "low": parse_num(data.get("lowPrice") or data.get("low")),
        "close": parse_num(data.get("closePrice") or data.get("close") or price),
        "volume": int(volume) if volume is not None else None,
        "turnover": turnover,
        "vwap": parse_num(data.get("averagePrice") or data.get("vwap")),
        "change": parse_num(data.get("change") or data.get("priceChange")),
        "change_pct": parse_num(data.get("changePercent") or data.get("changeRate")),
        "source": "fugle_rest",
        "last_updated_phase": "intraday",
        "data_status": "partial",
        "raw_json": json.dumps(payload, ensure_ascii=False)[:8000],
    }


def refresh_watchlist_intraday(
    codes: list[str] | None = None,
    *,
    mode: str | None = None,
    once: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    selected_mode = (mode or FUGLE_INTRADAY_MODE or "disabled").strip().lower()
    if selected_mode not in {"websocket_candles", "websocket_trades", "rest_quote_polling", "disabled"}:
        selected_mode = "disabled"
    input_codes = codes or get_watchlist_codes()
    normalized = []
    seen = set()
    for raw in input_codes:
        code = str(raw or "").strip().zfill(4)[:4]
        if code.isdigit() and code not in seen:
            seen.add(code)
            normalized.append(code)
    stats = {
        "ok": True,
        "mode": selected_mode,
        "watchlist_only": True,
        "writes_db": bool(not dry_run and selected_mode == "rest_quote_polling"),
        "dry_run": dry_run,
        "total": len(normalized),
        "updated": 0,
        "skipped": 0,
        "delayed": [],
        "errors": [],
    }
    if selected_mode == "disabled":
        stats["writes_db"] = False
        stats["reason"] = "intraday_mode_disabled"
        return stats
    if not market_is_open_now():
        stats["writes_db"] = False
        stats["reason"] = "outside_taiwan_market_hours"
        return stats
    if selected_mode in {"websocket_candles", "websocket_trades"}:
        # WebSocket package is intentionally not introduced in v1 unless a
        # stable dependency is already available.  Keep the mode non-crashing.
        stats["writes_db"] = False
        stats["reason"] = "websocket_mode_not_enabled_in_v1"
        stats["delayed"] = normalized[FUGLE_MAX_WATCHLIST_SYMBOLS:]
        return stats
    batches = normalized
    if len(batches) > FUGLE_MAX_WATCHLIST_SYMBOLS:
        stats["delayed"] = batches[FUGLE_MAX_WATCHLIST_SYMBOLS:]
    for idx, code in enumerate(batches):
        try:
            payload = fetch_fugle_quote_network(code)
            snapshot = _parse_fugle_quote_snapshot(code, payload)
            if not snapshot:
                stats["skipped"] += 1
                continue
            if upsert_intraday_snapshot(snapshot, dry_run=dry_run):
                stats["updated"] += 1
        except Exception as exc:
            stats["errors"].append({"code": code, "error": str(exc)})
        if not once and idx < len(batches) - 1:
            time.sleep(max(1, int(FUGLE_INTRADAY_POLL_SECONDS)))
    if not dry_run:
        cleanup_intraday_1m_history()
    return stats
