from __future__ import annotations

import sqlite3

from core.market_timing import availability_contract


def _add_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(official_company_event)")}
    if name not in columns:
        conn.execute(f"ALTER TABLE official_company_event ADD COLUMN {name} {definition}")


def ensure_official_event_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS official_company_event (
            event_key TEXT PRIMARY KEY,
            disclosed_date TEXT NOT NULL,
            disclosed_time TEXT,
            fact_date TEXT,
            code TEXT NOT NULL,
            company_name TEXT,
            subject TEXT NOT NULL,
            explanation TEXT,
            article_code TEXT,
            market TEXT NOT NULL,
            attention_level TEXT NOT NULL DEFAULT 'normal',
            source TEXT NOT NULL,
            source_quality TEXT NOT NULL DEFAULT 'official',
            fetched_at TEXT NOT NULL,
            available_at TEXT,
            market_session TEXT NOT NULL DEFAULT 'unknown',
            effective_tw_trade_date TEXT,
            CHECK(market IN ('listed', 'otc')),
            CHECK(attention_level IN ('normal', 'attention'))
        );
        CREATE INDEX IF NOT EXISTS idx_official_event_code_date
            ON official_company_event(code, disclosed_date DESC, disclosed_time DESC);
        CREATE INDEX IF NOT EXISTS idx_official_event_date
            ON official_company_event(disclosed_date DESC);
        """
    )
    _add_column(conn, "available_at", "TEXT")
    _add_column(conn, "market_session", "TEXT NOT NULL DEFAULT 'unknown'")
    _add_column(conn, "effective_tw_trade_date", "TEXT")
    conn.execute(
        """
        UPDATE official_company_event
        SET disclosed_time=substr('000000' || trim(disclosed_time), -6, 6)
        WHERE disclosed_time IS NOT NULL
          AND trim(disclosed_time)<>''
          AND length(trim(disclosed_time)) BETWEEN 1 AND 5
          AND trim(disclosed_time) NOT GLOB '*[^0-9]*'
        """
    )
    missing_timing = conn.execute(
        """
        SELECT event_key,fetched_at
        FROM official_company_event
        WHERE available_at IS NULL OR market_session='unknown'
           OR effective_tw_trade_date IS NULL
        """
    ).fetchall()
    timing_rows = []
    for event_key, fetched_at in missing_timing:
        timing = availability_contract(fetched_at=fetched_at)
        timing_rows.append(
            (
                timing["available_at"],
                timing["market_session"],
                timing["effective_tw_trade_date"],
                event_key,
            )
        )
    if timing_rows:
        conn.executemany(
            """
            UPDATE official_company_event
            SET available_at=?,market_session=?,effective_tw_trade_date=?
            WHERE event_key=?
            """,
            timing_rows,
        )
