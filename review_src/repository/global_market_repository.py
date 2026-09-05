from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from core.global_market_schema import ensure_global_market_schema
from core.market_timing import next_taiwan_trading_date


def upsert_global_market_rows(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> int:
    ensure_global_market_schema(conn)
    values = list(rows)
    if not values:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO global_market_daily_snapshot(
            market_date,ticker,display_name,close,previous_close,change_pct,
            currency,source,source_quality,fetched_at,available_at,market_session,
            effective_tw_trade_date,exchange_timezone,source_market_timestamp
        ) VALUES(
            :market_date,:ticker,:display_name,:close,:previous_close,:change_pct,
            :currency,:source,:source_quality,:fetched_at,:available_at,:market_session,
            :effective_tw_trade_date,:exchange_timezone,:source_market_timestamp
        )
        ON CONFLICT(market_date,ticker) DO UPDATE SET
            display_name=excluded.display_name,
            close=excluded.close,
            previous_close=excluded.previous_close,
            change_pct=excluded.change_pct,
            currency=excluded.currency,
            source=excluded.source,
            source_quality=excluded.source_quality,
            fetched_at=excluded.fetched_at,
            available_at=excluded.available_at,
            market_session=excluded.market_session,
            effective_tw_trade_date=excluded.effective_tw_trade_date,
            exchange_timezone=excluded.exchange_timezone,
            source_market_timestamp=excluded.source_market_timestamp
        """,
        values,
    )
    return conn.total_changes - before


def prune_global_market_rows(
    conn: sqlite3.Connection,
    *,
    retain_market_days: int = 400,
) -> int:
    ensure_global_market_schema(conn)
    dates = [
        str(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT market_date
            FROM global_market_daily_snapshot
            ORDER BY market_date DESC
            LIMIT ?
            """,
            (max(int(retain_market_days), 1),),
        ).fetchall()
    ]
    if len(dates) < max(int(retain_market_days), 1):
        return 0
    before = conn.total_changes
    conn.execute(
        "DELETE FROM global_market_daily_snapshot WHERE market_date<?",
        (dates[-1],),
    )
    return conn.total_changes - before


def read_latest_global_market_rows(
    conn: sqlite3.Connection,
    *,
    reference_date: str,
) -> list[dict[str, Any]]:
    """Read the most recent persisted global close on or before a reference date."""

    effective_date = next_taiwan_trading_date(reference_date, strictly_after=True)
    latest = conn.execute(
        """
        SELECT MAX(market_date) AS latest_date
        FROM global_market_daily_snapshot
        WHERE market_date<=?
          AND effective_tw_trade_date<=?
        """,
        (reference_date, effective_date),
    ).fetchone()
    latest_date = str(latest["latest_date"] or "") if latest else ""
    if not latest_date:
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT market_date,ticker,display_name,close,previous_close,change_pct,
                   currency,source,source_quality,fetched_at,available_at,market_session,
                   effective_tw_trade_date,exchange_timezone,source_market_timestamp
            FROM global_market_daily_snapshot
            WHERE market_date=?
              AND effective_tw_trade_date<=?
            ORDER BY ticker
            """,
            (latest_date, effective_date),
        ).fetchall()
    ]


def read_global_market_rows_at_cutoff(
    conn: sqlite3.Connection,
    *,
    analysis_cutoff: str,
    tickers: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Read the latest visible row per ticker without creating or repairing schema."""

    try:
        cutoff = datetime.fromisoformat(str(analysis_cutoff).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("analysis_cutoff must be an ISO-8601 timestamp") from exc
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("analysis_cutoff must include an explicit UTC offset")
    cutoff = cutoff.astimezone(timezone.utc)
    requested = (
        {str(ticker).strip() for ticker in tickers if str(ticker).strip()}
        if tickers is not None
        else None
    )
    try:
        cursor = conn.execute(
            """
            SELECT market_date,ticker,display_name,close,previous_close,change_pct,
                   currency,source,source_quality,fetched_at,available_at,market_session,
                   effective_tw_trade_date,exchange_timezone,source_market_timestamp
            FROM global_market_daily_snapshot
            WHERE available_at IS NOT NULL
            ORDER BY market_date DESC,available_at DESC,ticker
            """
        )
    except sqlite3.OperationalError:
        return []
    columns = [str(column[0]) for column in cursor.description or ()]
    selected: dict[str, dict[str, Any]] = {}
    for source in cursor.fetchall():
        row = dict(source) if isinstance(source, sqlite3.Row) else dict(
            zip(columns, source, strict=True)
        )
        ticker = str(row.get("ticker") or "")
        if requested is not None and ticker not in requested:
            continue
        try:
            available = datetime.fromisoformat(
                str(row.get("available_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if available.tzinfo is None or available.utcoffset() is None:
            continue
        if available.astimezone(timezone.utc) <= cutoff and ticker not in selected:
            selected[ticker] = row
    return [selected[ticker] for ticker in sorted(selected)]
