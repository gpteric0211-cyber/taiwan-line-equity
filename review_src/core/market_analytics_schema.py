from __future__ import annotations

import sqlite3


TECHNICAL_FORMULA_VERSION = "scoring-v1.0.4-asof-260"
TECHNICAL_LOOKBACK_ROWS = 260


def ensure_market_analytics_schema(conn: sqlite3.Connection) -> None:
    """Create the normalized full-market master and derived daily snapshot tables.

    Raw OHLCV remains canonical in ``history_price`` and official valuation
    remains canonical in ``twse_daily_valuation``.  The snapshot table stores
    derived technical values only, so there is no competing copy of market
    prices or valuation data.
    """

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock_master (
            code TEXT PRIMARY KEY,
            name TEXT,
            market TEXT NOT NULL,
            exchange TEXT NOT NULL,
            security_type TEXT NOT NULL DEFAULT 'stock',
            is_active INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL,
            source_status TEXT NOT NULL DEFAULT 'ok',
            first_seen_date TEXT,
            last_seen_date TEXT,
            updated_at TEXT NOT NULL,
            CHECK(market IN ('listed', 'otc')),
            CHECK(exchange IN ('TWSE', 'TPEX')),
            CHECK(is_active IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS idx_stock_master_market_active
            ON stock_master(market, is_active, code);

        CREATE TABLE IF NOT EXISTS daily_technical_snapshot (
            trade_date TEXT NOT NULL,
            code TEXT NOT NULL,
            formula_version TEXT NOT NULL,
            input_row_count INTEGER NOT NULL,
            input_start_date TEXT,
            input_end_date TEXT NOT NULL,
            adjustment_event_count INTEGER NOT NULL DEFAULT 0,
            ma5 REAL,
            ma10 REAL,
            ma20 REAL,
            ma60 REAL,
            ema12 REAL,
            ema26 REAL,
            rsi5 REAL,
            rsi10 REAL,
            rsi14 REAL,
            macd_dif REAL,
            macd_signal REAL,
            macd_osc REAL,
            kd_k REAL,
            kd_d REAL,
            atr14 REAL,
            boll_mid REAL,
            boll_upper REAL,
            boll_lower REAL,
            boll_width REAL,
            obv REAL,
            volume_ma5 REAL,
            volume_ma20 REAL,
            previous_10d_low REAL,
            previous_20d_low REAL,
            previous_20d_high REAL,
            previous_60d_high REAL,
            history_source TEXT,
            source_quality TEXT NOT NULL,
            data_quality TEXT NOT NULL,
            decision_ready INTEGER NOT NULL DEFAULT 0,
            quality_reason TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, code),
            CHECK(decision_ready IN (0, 1))
        );
        CREATE INDEX IF NOT EXISTS idx_daily_technical_snapshot_code_date
            ON daily_technical_snapshot(code, trade_date DESC);
        CREATE INDEX IF NOT EXISTS idx_daily_technical_snapshot_date_quality
            ON daily_technical_snapshot(trade_date DESC, data_quality, code);
        """
    )
