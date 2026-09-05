from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from core.market_analytics_schema import ensure_market_analytics_schema
from repository.full_market_batch_repository import resolve_full_market_analysis_date


TECHNICAL_FIELDS = (
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ema12",
    "ema26",
    "rsi5",
    "rsi10",
    "rsi14",
    "macd_dif",
    "macd_signal",
    "macd_osc",
    "kd_k",
    "kd_d",
    "atr14",
    "boll_mid",
    "boll_upper",
    "boll_lower",
    "boll_width",
    "obv",
    "volume_ma5",
    "volume_ma20",
    "previous_10d_low",
    "previous_20d_low",
    "previous_20d_high",
    "previous_60d_high",
)


def upsert_stock_master_rows(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> int:
    ensure_market_analytics_schema(conn)
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO stock_master(
            code,name,market,exchange,security_type,is_active,source,
            source_status,first_seen_date,last_seen_date,updated_at
        ) VALUES(
            :code,:name,:market,:exchange,:security_type,:is_active,:source,
            :source_status,:first_seen_date,:last_seen_date,:updated_at
        )
        ON CONFLICT(code) DO UPDATE SET
            name=CASE
                WHEN excluded.name IS NOT NULL AND TRIM(excluded.name)<>'' THEN excluded.name
                ELSE stock_master.name
            END,
            market=excluded.market,
            exchange=excluded.exchange,
            security_type=excluded.security_type,
            is_active=excluded.is_active,
            source=excluded.source,
            source_status=excluded.source_status,
            first_seen_date=CASE
                WHEN stock_master.first_seen_date IS NULL THEN excluded.first_seen_date
                WHEN excluded.first_seen_date IS NULL THEN stock_master.first_seen_date
                ELSE MIN(stock_master.first_seen_date, excluded.first_seen_date)
            END,
            last_seen_date=CASE
                WHEN stock_master.last_seen_date IS NULL THEN excluded.last_seen_date
                WHEN excluded.last_seen_date IS NULL THEN stock_master.last_seen_date
                ELSE MAX(stock_master.last_seen_date, excluded.last_seen_date)
            END,
            updated_at=excluded.updated_at
        """,
        list(rows),
    )
    return conn.total_changes - before


def active_stock_codes(
    conn: sqlite3.Connection,
    *,
    market: str | None = None,
) -> list[str]:
    ensure_market_analytics_schema(conn)
    if market:
        rows = conn.execute(
            "SELECT code FROM stock_master WHERE is_active=1 AND market=? ORDER BY code",
            (market,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT code FROM stock_master WHERE is_active=1 ORDER BY market,code"
        ).fetchall()
    return [str(row[0]) for row in rows]


def history_rows_for_technicals(
    conn: sqlite3.Connection,
    code: str,
    *,
    end_date: str | None = None,
    limit: int = 600,
) -> list[dict[str, Any]]:
    selected_end_date = resolve_full_market_analysis_date(conn, end_date)
    if not selected_end_date:
        return []
    params: list[Any] = [str(code).zfill(4)]
    date_filter = " AND date<=?"
    params.append(selected_end_date)
    params.append(max(int(limit), 1))
    rows = conn.execute(
        f"""
        SELECT date,code,open,high,low,close,volume,volume_unit,
               source,source_quality,market
        FROM history_price
        WHERE code=?{date_filter}
        ORDER BY date DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    normalized: list[dict[str, Any]] = []
    for source_row in reversed(rows):
        row = dict(source_row)
        if str(row.get("volume_unit") or "shares").strip().lower() == "lots":
            row["volume"] = float(row["volume"]) * 1000 if row.get("volume") is not None else None
            row["volume_unit"] = "shares"
        normalized.append(row)
    return normalized


def all_history_rows_for_technical_ensemble(
    conn: sqlite3.Connection,
    code: str,
    *,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """Read the complete available prefix so cumulative indicators never rebase."""

    selected_end_date = resolve_full_market_analysis_date(conn, end_date)
    if not selected_end_date:
        return []
    rows = conn.execute(
        """
        SELECT date,code,open,high,low,close,volume,volume_unit,
               source,source_quality,market
        FROM history_price
        WHERE code=? AND date<=?
        ORDER BY date
        """,
        (str(code).zfill(4), selected_end_date),
    ).fetchall()
    normalized: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        if str(row.get("volume_unit") or "shares").strip().lower() == "lots":
            row["volume"] = float(row["volume"]) * 1000 if row.get("volume") is not None else None
            row["volume_unit"] = "shares"
        normalized.append(row)
    return normalized


def upsert_daily_technical_snapshots(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> int:
    ensure_market_analytics_schema(conn)
    values = list(rows)
    if not values:
        return 0
    before = conn.total_changes
    columns = (
        "trade_date",
        "code",
        "formula_version",
        "input_row_count",
        "input_start_date",
        "input_end_date",
        "adjustment_event_count",
        *TECHNICAL_FIELDS,
        "history_source",
        "source_quality",
        "data_quality",
        "decision_ready",
        "quality_reason",
        "computed_at",
    )
    column_sql = ",".join(columns)
    value_sql = ",".join(f":{column}" for column in columns)
    update_sql = ",".join(
        f"{column}=excluded.{column}" for column in columns if column not in {"trade_date", "code"}
    )
    conn.executemany(
        f"""
        INSERT INTO daily_technical_snapshot({column_sql})
        VALUES({value_sql})
        ON CONFLICT(trade_date,code) DO UPDATE SET {update_sql}
        """,
        values,
    )
    return conn.total_changes - before


def prune_daily_technical_snapshots(
    conn: sqlite3.Connection,
    *,
    retain_trading_days: int = 600,
) -> dict[str, Any]:
    ensure_market_analytics_schema(conn)
    dates = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT trade_date
            FROM daily_technical_snapshot
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (max(int(retain_trading_days), 1),),
        ).fetchall()
    ]
    if len(dates) < max(int(retain_trading_days), 1):
        return {"cutoff_trade_date": dates[-1] if dates else None, "deleted_rows": 0}
    cutoff = dates[-1]
    before = conn.total_changes
    conn.execute("DELETE FROM daily_technical_snapshot WHERE trade_date<?", (cutoff,))
    return {"cutoff_trade_date": cutoff, "deleted_rows": conn.total_changes - before}
