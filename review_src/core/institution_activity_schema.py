from __future__ import annotations

import sqlite3


def ensure_institution_activity_schema(conn: sqlite3.Connection) -> None:
    """Create the official gross/net three-institution activity table."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS institution_activity_daily (
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            market TEXT NOT NULL,
            foreign_buy INTEGER,
            foreign_sell INTEGER,
            foreign_net INTEGER,
            trust_buy INTEGER,
            trust_sell INTEGER,
            trust_net INTEGER,
            dealer_buy INTEGER,
            dealer_sell INTEGER,
            dealer_net INTEGER,
            source TEXT NOT NULL,
            source_quality TEXT NOT NULL DEFAULT 'official',
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, code),
            CHECK(market IN ('listed', 'otc'))
        );
        CREATE INDEX IF NOT EXISTS idx_institution_activity_code_date
            ON institution_activity_daily(code, trade_date DESC);
        """
    )
    legacy_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='institution_daily'"
    ).fetchone()
    if legacy_table:
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(institution_daily)").fetchall()
        }
        if "source_quality" not in columns:
            conn.execute("ALTER TABLE institution_daily ADD COLUMN source_quality TEXT")
        if "fetched_at" not in columns:
            conn.execute("ALTER TABLE institution_daily ADD COLUMN fetched_at REAL")
