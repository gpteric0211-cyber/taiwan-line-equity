from __future__ import annotations

import sqlite3

from core.market_timing import availability_contract


def _add_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(news_radar_event)").fetchall()
    }
    if name not in columns:
        conn.execute(f"ALTER TABLE news_radar_event ADD COLUMN {name} {definition}")


def ensure_news_radar_schema(conn: sqlite3.Connection) -> None:
    """Create a lead-only news index that can never enter the referee layer."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS news_radar_event (
            event_key TEXT PRIMARY KEY,
            event_date TEXT NOT NULL,
            published_at TEXT NOT NULL,
            source_id TEXT NOT NULL,
            publisher TEXT NOT NULL,
            source_url TEXT NOT NULL,
            title TEXT NOT NULL,
            affected_terms_json TEXT NOT NULL DEFAULT '[]',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            quality_status TEXT NOT NULL DEFAULT 'ok',
            source_quality TEXT NOT NULL DEFAULT 'supplemental',
            verification_status TEXT NOT NULL DEFAULT 'unverified',
            license_class TEXT NOT NULL,
            analysis_version TEXT NOT NULL,
            content_fingerprint TEXT NOT NULL,
            reliability_score REAL NOT NULL DEFAULT 0,
            reference_value_score REAL NOT NULL DEFAULT 0,
            can_override_main_status INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL,
            available_at TEXT,
            market_session TEXT NOT NULL DEFAULT 'unknown',
            effective_tw_trade_date TEXT,
            CHECK(quality_status IN ('ok','stale','source_delayed','unavailable')),
            CHECK(source_quality='supplemental'),
            CHECK(verification_status='unverified'),
            CHECK(reliability_score>=0 AND reliability_score<=1),
            CHECK(reference_value_score>=0 AND reference_value_score<=1),
            CHECK(can_override_main_status=0)
        );
        CREATE INDEX IF NOT EXISTS idx_news_radar_event_date
            ON news_radar_event(event_date DESC, published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_news_radar_event_source
            ON news_radar_event(source_id, event_date DESC);
        """
    )
    _add_column(conn, "available_at", "TEXT")
    _add_column(conn, "market_session", "TEXT NOT NULL DEFAULT 'unknown'")
    _add_column(conn, "effective_tw_trade_date", "TEXT")
    legacy_rows = conn.execute(
        """
        SELECT event_key,published_at,fetched_at
        FROM news_radar_event
        WHERE available_at IS NULL OR effective_tw_trade_date IS NULL
        """
    ).fetchall()
    for row in legacy_rows:
        timing = availability_contract(fetched_at=row[2], published_at=row[1])
        conn.execute(
            """
            UPDATE news_radar_event
            SET available_at=?,market_session=?,effective_tw_trade_date=?
            WHERE event_key=?
            """,
            (
                timing["available_at"],
                timing["market_session"],
                timing["effective_tw_trade_date"],
                row[0],
            ),
        )
