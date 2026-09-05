from __future__ import annotations

import sqlite3


FULL_MARKET_BATCH_CONTRACT_VERSION = "full-market-completion-v1"


def ensure_full_market_batch_schema(conn: sqlite3.Connection) -> None:
    """Create the write-path schema for audited full-market completion markers.

    Callers must invoke this before starting the transaction that stores
    market rows and the completion marker.  ``executescript`` is kept out of
    that transaction because SQLite may finalize a pending transaction first.
    """

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS full_market_batch_runs (
            run_id TEXT PRIMARY KEY,
            trade_date TEXT NOT NULL,
            batch_status TEXT NOT NULL,
            storage_status TEXT NOT NULL,
            expected_active_asof INTEGER NOT NULL,
            classified_ohlcv_count INTEGER NOT NULL,
            official_no_trade_count INTEGER NOT NULL,
            not_applicable_count INTEGER NOT NULL,
            classified_count INTEGER NOT NULL,
            unclassified_count INTEGER NOT NULL,
            overlap_count INTEGER NOT NULL,
            expected_universe_hash TEXT NOT NULL,
            classified_universe_hash TEXT NOT NULL,
            unclassified_codes_json TEXT NOT NULL,
            overlap_codes_json TEXT NOT NULL,
            source_summary_json TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finalized_at TEXT NOT NULL,
            CHECK(batch_status IN ('partial', 'complete')),
            CHECK(storage_status IN ('stored', 'not_stored', 'backfill_verified')),
            CHECK(expected_active_asof >= 0),
            CHECK(classified_ohlcv_count >= 0),
            CHECK(official_no_trade_count >= 0),
            CHECK(not_applicable_count >= 0),
            CHECK(classified_count = classified_ohlcv_count + official_no_trade_count + not_applicable_count),
            CHECK(unclassified_count >= 0),
            CHECK(overlap_count >= 0),
            CHECK(
                batch_status = 'partial'
                OR (
                    expected_active_asof > 0
                    AND unclassified_count = 0
                    AND overlap_count = 0
                    AND classified_count = expected_active_asof
                )
            )
        );
        CREATE INDEX IF NOT EXISTS idx_full_market_batch_runs_date_status
            ON full_market_batch_runs(trade_date DESC, batch_status, finalized_at DESC);

        CREATE TABLE IF NOT EXISTS full_market_batch_publications (
            trade_date TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            expected_active_asof INTEGER NOT NULL,
            classified_ohlcv_count INTEGER NOT NULL,
            official_no_trade_count INTEGER NOT NULL,
            not_applicable_count INTEGER NOT NULL,
            expected_universe_hash TEXT NOT NULL,
            classified_universe_hash TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            published_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES full_market_batch_runs(run_id),
            CHECK(expected_active_asof > 0),
            CHECK(
                expected_active_asof =
                    classified_ohlcv_count + official_no_trade_count + not_applicable_count
            )
        );
        CREATE INDEX IF NOT EXISTS idx_full_market_batch_publications_date
            ON full_market_batch_publications(trade_date DESC);

        CREATE TABLE IF NOT EXISTS full_market_not_applicable_daily (
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            source TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_quality TEXT NOT NULL DEFAULT 'official',
            evidence_json TEXT NOT NULL,
            verified_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, code),
            CHECK(reason_code IN (
                'official_listing_not_effective',
                'official_delisting_effective',
                'official_security_not_applicable'
            )),
            CHECK(LOWER(source_quality) = 'official'),
            CHECK(LENGTH(TRIM(source)) > 0),
            CHECK(LENGTH(TRIM(source_url)) > 0),
            CHECK(LENGTH(TRIM(evidence_json)) > 2)
        );
        """
    )
