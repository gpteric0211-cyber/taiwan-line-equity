from __future__ import annotations

import sqlite3


def ensure_company_size_schema(conn: sqlite3.Connection) -> None:
    """Create point-in-time official company-size snapshots."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS official_company_size_snapshot (
            data_date TEXT NOT NULL,
            code TEXT NOT NULL,
            market TEXT NOT NULL,
            paid_in_capital_twd REAL,
            issued_shares REAL,
            source_id TEXT NOT NULL,
            source_quality TEXT NOT NULL DEFAULT 'official',
            fetched_at TEXT NOT NULL,
            PRIMARY KEY(data_date, code),
            CHECK(market IN ('listed', 'otc')),
            CHECK(source_quality='official'),
            CHECK(paid_in_capital_twd IS NULL OR paid_in_capital_twd>=0),
            CHECK(issued_shares IS NULL OR issued_shares>=0)
        );
        CREATE INDEX IF NOT EXISTS idx_company_size_code_date
            ON official_company_size_snapshot(code, data_date DESC);
        """
    )
