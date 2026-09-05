from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any


SOURCE_STATUS_PRIORITY = {
    "ok": 0,
    "source_delayed": 1,
    "stale": 2,
    "cannot_verify": 3,
}


def parse_trade_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("/", "-")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_market_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"listed", "twse", "tse"} or "上市" in text:
        return "listed"
    if text in {"otc", "tpex"} or "上櫃" in text:
        return "otc"
    return None


def normalize_valuation_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if text.isdigit() and len(text) < 4:
        return text.zfill(4)
    return text


def _source_market_from_row(row: dict[str, Any]) -> str | None:
    source_text = f"{row.get('source') or ''} {row.get('source_type') or ''} {row.get('source_market') or ''}".lower()
    if "tpex" in source_text or "otc" in source_text:
        return "otc"
    if "twse" in source_text or "bwibbu" in source_text or "listed" in source_text:
        return "listed"
    return None


def market_type_for_code(conn: sqlite3.Connection, code: str, row: dict[str, Any] | None = None) -> str | None:
    if row:
        market = normalize_market_type(row.get("market") or row.get("source_market"))
        if market:
            return market
        market = _source_market_from_row(row)
        if market:
            return market
    try:
        profile = conn.execute(
            """
            SELECT market
            FROM stock_industry_profile
            WHERE code=?
            LIMIT 1
            """,
            (code,),
        ).fetchone()
    except sqlite3.OperationalError:
        profile = None
    if profile:
        return normalize_market_type(profile["market"] if hasattr(profile, "keys") else profile[0])
    return None


def _official_source_filter_sql(market_type: str) -> str:
    if market_type == "otc":
        return "UPPER(COALESCE(source, '')) LIKE '%TPEX%'"
    return "UPPER(COALESCE(source, '')) LIKE '%TWSE%'"


def latest_official_ohlcv_date(conn: sqlite3.Connection, market_type: str) -> str | None:
    sql = f"""
        SELECT MAX(date)
        FROM history_price
        WHERE date IS NOT NULL
          AND date <> ''
          AND {_official_source_filter_sql(market_type)}
    """
    try:
        return conn.execute(sql).fetchone()[0]
    except sqlite3.OperationalError:
        return None


def latest_stock_price_date(conn: sqlite3.Connection, code: str) -> str | None:
    try:
        return conn.execute(
            """
            SELECT MAX(date)
            FROM history_price
            WHERE code=?
              AND date IS NOT NULL
              AND date <> ''
            """,
            (code,),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return None


def effective_trade_day_gap(
    conn: sqlite3.Connection,
    start_date: Any,
    end_date: Any,
    market_type: str,
) -> int | None:
    start = parse_trade_date(start_date)
    end = parse_trade_date(end_date)
    if not start or not end or start > end:
        return None
    sql = f"""
        SELECT COUNT(DISTINCT date)
        FROM history_price
        WHERE date > ?
          AND date <= ?
          AND {_official_source_filter_sql(market_type)}
    """
    try:
        return int(conn.execute(sql, (start.isoformat(), end.isoformat())).fetchone()[0] or 0)
    except sqlite3.OperationalError:
        return None


def merge_source_status(*statuses: Any) -> str:
    selected = "ok"
    selected_score = SOURCE_STATUS_PRIORITY["ok"]
    for raw in statuses:
        status = str(raw or "ok").strip().lower()
        if not status:
            status = "ok"
        score = SOURCE_STATUS_PRIORITY.get(status)
        if score is None:
            if status != "ok" and selected_score == SOURCE_STATUS_PRIORITY["ok"]:
                selected = status
            continue
        if score > selected_score:
            selected = status
            selected_score = score
    return selected


def valuation_freshness_for_row(
    conn: sqlite3.Connection,
    code: Any,
    row: dict[str, Any] | sqlite3.Row | None,
) -> dict[str, Any]:
    data = dict(row) if row is not None else {}
    norm_code = normalize_valuation_code(code or data.get("symbol") or data.get("code"))
    flags: list[str] = []
    debug_reasons: list[str] = []
    market_type = market_type_for_code(conn, norm_code, data)
    valuation_date = data.get("data_date") or data.get("date")
    market_latest_date = latest_official_ohlcv_date(conn, market_type) if market_type else None
    price_date = latest_stock_price_date(conn, norm_code) if norm_code else None

    freshness_status = "ok"
    valuation_trade_day_gap: int | None = None
    price_trade_day_gap: int | None = None

    parsed_valuation = parse_trade_date(valuation_date)
    parsed_market_latest = parse_trade_date(market_latest_date)

    if not data:
        freshness_status = "cannot_verify"
        flags.append("valuation_row_missing")
        debug_reasons.append("valuation row missing")
    elif not norm_code:
        freshness_status = "cannot_verify"
        flags.append("valuation_code_missing")
        debug_reasons.append("cannot normalize stock code")
    elif not market_type:
        freshness_status = "cannot_verify"
        flags.append("market_type_cannot_verify")
        debug_reasons.append("cannot determine listed/otc market type")
    elif not parsed_valuation:
        freshness_status = "cannot_verify"
        flags.append("valuation_date_parse_failed")
        debug_reasons.append("valuation date is missing or unparsable")
    elif not parsed_market_latest:
        freshness_status = "cannot_verify"
        flags.append("official_ohlcv_latest_date_missing")
        debug_reasons.append("official market OHLCV latest date missing")
    elif parsed_valuation > parsed_market_latest:
        freshness_status = "cannot_verify"
        flags.append("valuation_date_ahead_of_market")
        debug_reasons.append("valuation date is ahead of official market OHLCV latest date")
    else:
        valuation_trade_day_gap = effective_trade_day_gap(conn, valuation_date, market_latest_date, market_type)
        if valuation_trade_day_gap is None:
            freshness_status = "cannot_verify"
            flags.append("valuation_trade_day_gap_cannot_verify")
            debug_reasons.append("cannot calculate valuation effective trade-day gap")
        elif valuation_trade_day_gap > 1:
            freshness_status = "stale"
            flags.extend(["source_delayed", "valuation_stale"])
            debug_reasons.append(
                f"valuation_date lags official {market_type} OHLCV latest by {valuation_trade_day_gap} effective trade days"
            )

    if market_type and price_date and market_latest_date:
        parsed_price = parse_trade_date(price_date)
        if parsed_price and parsed_market_latest:
            if parsed_price > parsed_market_latest:
                flags.append("official_price_source_may_be_stale")
                debug_reasons.append(
                    "stock price canary date is ahead of official market OHLCV latest date"
                )
            else:
                price_trade_day_gap = effective_trade_day_gap(conn, price_date, market_latest_date, market_type)
                if price_trade_day_gap is not None and price_trade_day_gap > 2:
                    flags.append("price_source_stale")
                    debug_reasons.append(
                        f"stock price date lags official {market_type} OHLCV latest by {price_trade_day_gap} effective trade days"
                    )

    original_status = data.get("source_status") or "ok"
    source_status = merge_source_status(original_status, freshness_status)
    return {
        "freshness_status": freshness_status,
        "source_status": source_status,
        "market_type": market_type,
        "market_latest_date": market_latest_date,
        "valuation_trade_day_gap": valuation_trade_day_gap,
        "price_date": price_date,
        "price_trade_day_gap": price_trade_day_gap,
        "valuation_flags": sorted(set(flags)),
        "stale_reason": "; ".join(debug_reasons) if debug_reasons else None,
        "debug_reason": "; ".join(debug_reasons) if debug_reasons else None,
    }
