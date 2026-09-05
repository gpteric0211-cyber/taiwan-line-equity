from __future__ import annotations

import sqlite3


LINE_MEMORY_SCHEMA_VERSION = "line-memory-v3"


def ensure_line_memory_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS line_memory_subject (
            scope_key TEXT PRIMARY KEY,
            principal_key TEXT NOT NULL,
            source_type TEXT NOT NULL,
            key_version INTEGER NOT NULL,
            privacy_notice_version TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_line_memory_subject_principal
            ON line_memory_subject(principal_key);
        CREATE INDEX IF NOT EXISTS idx_line_memory_subject_expiry
            ON line_memory_subject(expires_at);

        CREATE TABLE IF NOT EXISTS line_memory_session (
            scope_key TEXT PRIMARY KEY,
            state_ciphertext BLOB NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            last_event_timestamp INTEGER NOT NULL DEFAULT 0,
            last_event_key TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY(scope_key) REFERENCES line_memory_subject(scope_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS line_memory_exchange (
            exchange_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_key TEXT NOT NULL,
            event_key TEXT NOT NULL UNIQUE,
            message_key TEXT,
            event_timestamp INTEGER NOT NULL,
            message_type TEXT NOT NULL,
            stock_code TEXT,
            trade_date TEXT,
            facts_fingerprint TEXT,
            user_ciphertext BLOB NOT NULL,
            assistant_ciphertext BLOB NOT NULL,
            delivered_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            summarized_at REAL,
            deleted_at REAL,
            FOREIGN KEY(scope_key) REFERENCES line_memory_subject(scope_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_line_memory_exchange_scope_time
            ON line_memory_exchange(scope_key, event_timestamp DESC, exchange_id DESC);
        CREATE INDEX IF NOT EXISTS idx_line_memory_exchange_scope_stock
            ON line_memory_exchange(scope_key, stock_code, event_timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_line_memory_exchange_message
            ON line_memory_exchange(scope_key, message_key);
        CREATE INDEX IF NOT EXISTS idx_line_memory_exchange_expiry
            ON line_memory_exchange(expires_at);

        CREATE TABLE IF NOT EXISTS line_memory_summary (
            summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_key TEXT NOT NULL,
            stock_code TEXT NOT NULL DEFAULT '',
            summary_ciphertext BLOB NOT NULL,
            source_exchange_max_id INTEGER NOT NULL,
            source_exchange_count INTEGER NOT NULL,
            model_id TEXT NOT NULL,
            summary_version TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY(scope_key) REFERENCES line_memory_subject(scope_key) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_line_memory_summary_active
            ON line_memory_summary(scope_key, stock_code, active, summary_id DESC);

        CREATE TABLE IF NOT EXISTS line_memory_summary_source (
            summary_id INTEGER NOT NULL,
            exchange_id INTEGER NOT NULL,
            PRIMARY KEY(summary_id, exchange_id),
            FOREIGN KEY(summary_id) REFERENCES line_memory_summary(summary_id) ON DELETE CASCADE,
            FOREIGN KEY(exchange_id) REFERENCES line_memory_exchange(exchange_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS line_memory_projection (
            projection_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_key TEXT NOT NULL,
            projection_ciphertext BLOB NOT NULL,
            projection_version TEXT NOT NULL,
            turn_count INTEGER NOT NULL,
            token_count INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY(scope_key) REFERENCES line_memory_subject(scope_key) ON DELETE CASCADE,
            CHECK(turn_count >= 0 AND turn_count <= 12),
            CHECK(token_count >= 0 AND token_count <= 4000),
            CHECK(active IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS idx_line_memory_projection_active
            ON line_memory_projection(scope_key, active, projection_id DESC);
        CREATE INDEX IF NOT EXISTS idx_line_memory_projection_expiry
            ON line_memory_projection(expires_at);

        CREATE TABLE IF NOT EXISTS line_memory_event_receipt (
            event_key TEXT PRIMARY KEY,
            scope_key TEXT NOT NULL,
            event_timestamp INTEGER NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            started_at REAL NOT NULL,
            completed_at REAL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_line_memory_event_expiry
            ON line_memory_event_receipt(expires_at);

        CREATE TABLE IF NOT EXISTS line_memory_compaction_lease (
            scope_key TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            leased_until REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_line_memory_compaction_lease_expiry
            ON line_memory_compaction_lease(leased_until);

        CREATE TABLE IF NOT EXISTS line_memory_schema_state (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
            schema_version TEXT NOT NULL,
            applied_at REAL NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO line_memory_schema_state(singleton_id, schema_version, applied_at)
        VALUES(1, ?, unixepoch())
        ON CONFLICT(singleton_id) DO UPDATE SET
            schema_version=excluded.schema_version,
            applied_at=excluded.applied_at
        """,
        (LINE_MEMORY_SCHEMA_VERSION,),
    )
