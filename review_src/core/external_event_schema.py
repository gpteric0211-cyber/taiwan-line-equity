from __future__ import annotations

import sqlite3

from core.market_timing import availability_contract


def ensure_external_event_schema(conn: sqlite3.Connection) -> None:
    """Create the auditable, non-overriding external-event store."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS external_market_event (
            event_key TEXT PRIMARY KEY,
            event_date TEXT NOT NULL,
            published_at TEXT,
            code TEXT,
            source_id TEXT NOT NULL,
            publisher TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_class TEXT NOT NULL,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary_excerpt TEXT,
            direction TEXT NOT NULL DEFAULT 'unknown',
            confidence TEXT NOT NULL DEFAULT 'low',
            time_horizon TEXT NOT NULL DEFAULT 'unknown',
            affected_terms_json TEXT NOT NULL DEFAULT '[]',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            quality_status TEXT NOT NULL DEFAULT 'ok',
            source_quality TEXT NOT NULL,
            license_class TEXT NOT NULL,
            analysis_version TEXT NOT NULL,
            content_fingerprint TEXT NOT NULL,
            reliability_score REAL NOT NULL DEFAULT 0,
            reference_value_score REAL NOT NULL DEFAULT 0,
            can_override_main_status INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL,
            CHECK(code IS NULL OR (length(code)=4 AND code GLOB '[0-9][0-9][0-9][0-9]')),
            CHECK(direction IN ('positive','negative','mixed','neutral','unknown')),
            CHECK(confidence IN ('high','medium','low','unavailable')),
            CHECK(quality_status IN ('ok','stale','source_delayed','estimated','unavailable')),
            CHECK(source_quality IN ('official','licensed')),
            CHECK(reliability_score>=0 AND reliability_score<=1),
            CHECK(reference_value_score>=0 AND reference_value_score<=1),
            CHECK(can_override_main_status=0)
        );
        CREATE INDEX IF NOT EXISTS idx_external_event_code_date
            ON external_market_event(code, event_date DESC, published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_external_event_type_date
            ON external_market_event(event_type, event_date DESC);
        CREATE INDEX IF NOT EXISTS idx_external_event_date
            ON external_market_event(event_date DESC);
        """
    )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(external_market_event)").fetchall()}
    for name, definition in (
        ("content_fingerprint", "TEXT NOT NULL DEFAULT ''"),
        ("reliability_score", "REAL NOT NULL DEFAULT 0"),
        ("reference_value_score", "REAL NOT NULL DEFAULT 0"),
        ("available_at", "TEXT"),
        ("market_session", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("effective_tw_trade_date", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE external_market_event ADD COLUMN {name} {definition}")
    legacy_rows = conn.execute(
        """
        SELECT event_key,published_at,fetched_at
        FROM external_market_event
        WHERE available_at IS NULL OR effective_tw_trade_date IS NULL
        """
    ).fetchall()
    for row in legacy_rows:
        timing = availability_contract(fetched_at=row[2], published_at=row[1])
        conn.execute(
            """
            UPDATE external_market_event
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
