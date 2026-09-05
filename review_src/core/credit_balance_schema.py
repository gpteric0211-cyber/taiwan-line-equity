from __future__ import annotations

import sqlite3


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def ensure_credit_balance_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS credit_balance_daily (
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            market TEXT NOT NULL,
            margin_prev_balance_lots INTEGER,
            margin_buy_lots INTEGER,
            margin_sell_lots INTEGER,
            margin_cash_repayment_lots INTEGER,
            margin_balance_lots INTEGER,
            margin_delta_lots INTEGER,
            margin_utilization_pct REAL,
            margin_utilization_method TEXT,
            margin_limit_lots INTEGER,
            short_prev_balance_lots INTEGER,
            short_sell_lots INTEGER,
            short_buy_lots INTEGER,
            short_stock_repayment_lots INTEGER,
            short_balance_lots INTEGER,
            short_delta_lots INTEGER,
            short_utilization_pct REAL,
            short_utilization_method TEXT,
            short_limit_lots INTEGER,
            sbl_prev_balance_shares INTEGER,
            sbl_sell_shares INTEGER,
            sbl_return_shares INTEGER,
            sbl_adjust_shares INTEGER,
            sbl_balance_shares INTEGER,
            sbl_delta_shares INTEGER,
            margin_source TEXT,
            lending_source TEXT,
            source_quality TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            validation_passed_at TEXT NOT NULL,
            usable_from TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, code)
        );
        CREATE INDEX IF NOT EXISTS idx_credit_balance_code_date
            ON credit_balance_daily(code, trade_date DESC);
        CREATE INDEX IF NOT EXISTS idx_credit_balance_date_market
            ON credit_balance_daily(trade_date, market);
        """
    )
    _add_column(conn, "margin_daily", "margin_unit TEXT")
    _add_column(conn, "margin_daily", "short_unit TEXT")
    _add_column(conn, "lending_daily", "source_quality TEXT")
    _add_column(conn, "lending_daily", "fetched_at REAL")
    _add_column(conn, "lending_daily", "lending_unit TEXT")
    _add_column(conn, "credit_balance_daily", "margin_utilization_pct REAL")
    _add_column(conn, "credit_balance_daily", "margin_utilization_method TEXT")
    _add_column(conn, "credit_balance_daily", "margin_limit_lots INTEGER")
    _add_column(conn, "credit_balance_daily", "short_utilization_pct REAL")
    _add_column(conn, "credit_balance_daily", "short_utilization_method TEXT")
    _add_column(conn, "credit_balance_daily", "short_limit_lots INTEGER")
