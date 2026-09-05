from __future__ import annotations

import sqlite3


def ensure_taifex_night_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS taifex_night_daily_snapshot (
            trade_date TEXT NOT NULL,
            contract TEXT NOT NULL,
            contract_month TEXT NOT NULL,
            last REAL NOT NULL,
            change_pct REAL NOT NULL,
            volume REAL NOT NULL,
            trading_session TEXT NOT NULL,
            source TEXT NOT NULL,
            source_quality TEXT NOT NULL DEFAULT 'official',
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, contract),
            CHECK(last > 0),
            CHECK(volume >= 0)
        );
        CREATE INDEX IF NOT EXISTS idx_taifex_night_date
            ON taifex_night_daily_snapshot(trade_date DESC, contract);
        """
    )
