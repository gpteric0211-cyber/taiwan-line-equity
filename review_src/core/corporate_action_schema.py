from __future__ import annotations

import sqlite3


PERMANENT_CORPORATE_ACTION_TABLE = "corporate_action_permanent_event"


def ensure_corporate_action_schema(conn: sqlite3.Connection) -> None:
    """Create the permanent corporate-action authority and legacy projection.

    This function is write-side only.  Read paths must never call it because an
    ordinary Web/LINE read is not allowed to perform hidden schema migration.
    The existing ``corporate_actions`` table remains a compatibility projection
    for current analysis readers; the permanent table is the complete authority.
    """

    statements = (
        """
        CREATE TABLE IF NOT EXISTS corporate_actions (
            code TEXT,
            date TEXT,
            action_type TEXT,
            cash_dividend REAL,
            stock_dividend REAL,
            source TEXT,
            is_confirmed INTEGER DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY(code, date, action_type)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS corporate_action_permanent_event (
            event_id TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            company_name TEXT,
            action_type TEXT NOT NULL,
            effective_date TEXT NOT NULL,
            effective_session TEXT NOT NULL DEFAULT 'regular_market_open',
            time_precision TEXT NOT NULL DEFAULT 'date',
            adjustment_method TEXT NOT NULL,
            stock_distribution_ratio REAL,
            ratio_unit TEXT,
            cash_dividend_per_share REAL,
            share_count_factor REAL,
            pre_event_price_multiplier REAL,
            source_id TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_record_key TEXT NOT NULL,
            verification_status TEXT NOT NULL,
            publisher_published_at TEXT,
            first_seen_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            retention_class TEXT NOT NULL DEFAULT 'permanent_corporate_action',
            directional_weight_eligible INTEGER NOT NULL DEFAULT 0,
            fact_digest TEXT NOT NULL UNIQUE,
            UNIQUE(code, effective_date, action_type),
            CHECK(length(event_id) = 64),
            CHECK(code GLOB '[0-9][0-9][0-9][0-9]'),
            CHECK(action_type IN (
                'right', 'dividend', 'right_dividend', 'split',
                'capital_reduction', 'capital_increase', 'other'
            )),
            CHECK(effective_session IN (
                'regular_market_open', 'after_market_close', 'unknown'
            )),
            CHECK(time_precision = 'date'),
            CHECK(adjustment_method IN (
                'bonus_share_distribution', 'cash_dividend', 'rights_issue',
                'share_split', 'capital_reduction', 'none', 'other'
            )),
            CHECK(stock_distribution_ratio IS NULL OR stock_distribution_ratio >= 0),
            CHECK(cash_dividend_per_share IS NULL OR cash_dividend_per_share >= 0),
            CHECK(share_count_factor IS NULL OR share_count_factor > 0),
            CHECK(pre_event_price_multiplier IS NULL OR pre_event_price_multiplier > 0),
            CHECK(
                (stock_distribution_ratio IS NULL AND ratio_unit IS NULL)
                OR
                (stock_distribution_ratio IS NOT NULL
                 AND ratio_unit = 'new_shares_per_existing_share')
            ),
            CHECK(verification_status IN (
                'official_verified', 'secondary_corroborated', 'unverified'
            )),
            CHECK(retention_class = 'permanent_corporate_action'),
            CHECK(directional_weight_eligible IN (0, 1)),
            CHECK(length(fact_digest) = 64)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_permanent_corporate_action_code_date
            ON corporate_action_permanent_event(code, effective_date DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_permanent_corporate_action_available
            ON corporate_action_permanent_event(available_at, code)
        """,
    )
    # ``sqlite3.Connection.executescript`` commits an already-open transaction.
    # Execute each DDL statement separately so schema creation, canonical insert,
    # and the legacy projection remain one rollback-safe transaction.
    for statement in statements:
        conn.execute(statement)
