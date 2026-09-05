from __future__ import annotations

import sqlite3


def ensure_trading_restriction_schema(conn: sqlite3.Connection) -> None:
    """Create append-only official trading-restriction snapshots.

    The source-run table proves that an empty result was observed from every
    required official endpoint.  GET/read paths must never call this helper.
    """

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS official_trading_restriction (
            event_key TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            company_name TEXT,
            market TEXT NOT NULL,
            restriction_type TEXT NOT NULL,
            announcement_date TEXT NOT NULL,
            effective_from TEXT,
            effective_to TEXT,
            reason TEXT,
            source_id TEXT NOT NULL,
            source_quality TEXT NOT NULL DEFAULT 'official',
            fetched_at TEXT NOT NULL,
            CHECK(market IN ('listed', 'otc')),
            CHECK(restriction_type IN (
                'attention', 'disposition', 'altered_trading',
                'periodic_trading', 'managed_stock', 'trading_halt'
            )),
            CHECK(source_quality='official')
        );
        CREATE INDEX IF NOT EXISTS idx_trading_restriction_code_date
            ON official_trading_restriction(
                code, announcement_date DESC, effective_from, effective_to
            );
        CREATE INDEX IF NOT EXISTS idx_trading_restriction_market_date
            ON official_trading_restriction(market, announcement_date DESC);

        CREATE TABLE IF NOT EXISTS official_trading_restriction_source_run (
            data_date TEXT NOT NULL,
            market TEXT NOT NULL,
            source_id TEXT NOT NULL,
            status TEXT NOT NULL,
            rows_received INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL,
            error_summary TEXT,
            PRIMARY KEY(data_date, source_id),
            CHECK(market IN ('listed', 'otc')),
            CHECK(status IN ('ok', 'failed'))
        );
        CREATE INDEX IF NOT EXISTS idx_trading_restriction_run_date
            ON official_trading_restriction_source_run(data_date DESC, market);
        """
    )
