from __future__ import annotations

import sqlite3

from core.market_timing import availability_contract


def _add_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(global_market_daily_snapshot)").fetchall()
    }
    if name not in columns:
        conn.execute(
            f"ALTER TABLE global_market_daily_snapshot ADD COLUMN {name} {definition}"
        )


def ensure_global_market_schema(conn: sqlite3.Connection) -> None:
    """Create the supplemental US close snapshot table.

    The table is written only by explicit update tasks.  Stock/detail GET paths
    and the LINE market-data API remain read-only.
    """

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS global_market_daily_snapshot (
            market_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            display_name TEXT NOT NULL,
            close REAL NOT NULL,
            previous_close REAL,
            change_pct REAL,
            currency TEXT NOT NULL DEFAULT 'USD',
            source TEXT NOT NULL,
            source_quality TEXT NOT NULL DEFAULT 'supplemental',
            fetched_at TEXT NOT NULL,
            available_at TEXT,
            market_session TEXT NOT NULL DEFAULT 'unknown',
            effective_tw_trade_date TEXT,
            exchange_timezone TEXT,
            source_market_timestamp TEXT,
            PRIMARY KEY(market_date, ticker),
            CHECK(close > 0)
        );
        CREATE INDEX IF NOT EXISTS idx_global_market_daily_snapshot_date
            ON global_market_daily_snapshot(market_date DESC, ticker);
        """
    )
    _add_column(conn, "available_at", "TEXT")
    _add_column(conn, "market_session", "TEXT NOT NULL DEFAULT 'unknown'")
    _add_column(conn, "effective_tw_trade_date", "TEXT")
    _add_column(conn, "exchange_timezone", "TEXT")
    _add_column(conn, "source_market_timestamp", "TEXT")
    legacy_rows = conn.execute(
        """
        SELECT market_date,ticker,fetched_at
        FROM global_market_daily_snapshot
        WHERE available_at IS NULL OR effective_tw_trade_date IS NULL
        """
    ).fetchall()
    for row in legacy_rows:
        timing = availability_contract(fetched_at=row[2])
        conn.execute(
            """
            UPDATE global_market_daily_snapshot
            SET available_at=?,market_session=?,effective_tw_trade_date=?
            WHERE market_date=? AND ticker=?
            """,
            (
                timing["available_at"],
                timing["market_session"],
                timing["effective_tw_trade_date"],
                row[0],
                row[1],
            ),
        )
