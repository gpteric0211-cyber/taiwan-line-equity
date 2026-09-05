from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from core.config import DATA_DIR, DB_PATH
from core.credit_balance_schema import ensure_credit_balance_schema
from core.external_event_schema import ensure_external_event_schema
from core.full_market_batch_schema import ensure_full_market_batch_schema
from core.market_analytics_schema import ensure_market_analytics_schema
from core.news_radar_schema import ensure_news_radar_schema
from core.global_market_schema import ensure_global_market_schema
from core.institution_activity_schema import ensure_institution_activity_schema
from core.official_event_schema import ensure_official_event_schema
from core.provenance_schema import ensure_provenance_schema
from core.single_track_v3_schema import ensure_single_track_v3_schema
from core.taifex_night_schema import ensure_taifex_night_schema
from core.trading_restriction_schema import ensure_trading_restriction_schema
from core.company_size_schema import ensure_company_size_schema
from auth.models import CREATE_TABLES_SQL as AUTH_CREATE_TABLES_SQL


RUNTIME_SCHEMA_VERSION = "2026-09-01.2"


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=FULL;")
    conn.row_factory = sqlite3.Row
    return conn


def read_only_db(path: Path | None = None) -> sqlite3.Connection:
    """Open an existing SQLite database without creating or mutating it."""

    target = (path or DB_PATH).resolve()
    uri = target.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA query_only=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def assert_db_integrity(path: Path | None = None, *, full: bool = False) -> None:
    """Fail closed when SQLite reports corruption.

    ``quick_check`` is appropriate for startup/preflight. Scheduled write
    postflight must pass ``full=True`` so every table and index is verified
    before the task may publish success.
    """

    target = (path or DB_PATH).resolve()
    if not target.exists() or target.stat().st_size == 0:
        return
    uri = target.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            pragma = "integrity_check" if full else "quick_check"
            rows = conn.execute(f"PRAGMA {pragma}").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"SQLite integrity check failed for {target.name}: {exc}") from exc
    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        check_name = "integrity_check" if full else "quick_check"
        raise RuntimeError(
            f"SQLite {check_name} failed for {target.name}: "
            + "; ".join(messages[:10])
        )


def runtime_schema_is_current(path: Path | None = None) -> bool:
    """Check the explicit schema marker without requesting a write lock."""

    target = (path or DB_PATH).resolve()
    if not target.exists() or target.stat().st_size == 0:
        return False
    uri = target.as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_schema_state'"
            ).fetchone()
            if not table:
                return False
            row = conn.execute(
                "SELECT schema_version FROM app_schema_state WHERE singleton_id=1"
            ).fetchone()
            return bool(row and str(row[0]) == RUNTIME_SCHEMA_VERSION)
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    assert_db_integrity()
    if runtime_schema_is_current():
        return
    with closing(db()) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                code TEXT PRIMARY KEY,
                name TEXT,
                sort_order INTEGER DEFAULT 0,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS eod_price (
                date TEXT,
                code TEXT,
                name TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                change_value REAL,
                transactions REAL,
                source TEXT,
                updated_at REAL,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS valuation (
                date TEXT,
                code TEXT,
                dividend_yield REAL,
                pe REAL,
                pb REAL,
                source TEXT,
                updated_at REAL,
                eps REAL,
                eps_source TEXT,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS stock_industry_profile (
                code TEXT PRIMARY KEY,
                name TEXT,
                market TEXT,
                industry TEXT,
                industry_code TEXT,
                source TEXT,
                quality TEXT,
                updated_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_stock_industry_profile_industry
                ON stock_industry_profile(industry);
            CREATE TABLE IF NOT EXISTS stock_theme_profile (
                code TEXT NOT NULL,
                tag_type TEXT NOT NULL,
                tag_name TEXT NOT NULL,
                source TEXT NOT NULL,
                source_key TEXT,
                quality TEXT,
                updated_at REAL,
                PRIMARY KEY (code, tag_type, tag_name, source)
            );
            CREATE INDEX IF NOT EXISTS idx_stock_theme_profile_code
                ON stock_theme_profile(code);
            CREATE INDEX IF NOT EXISTS idx_stock_theme_profile_tag
                ON stock_theme_profile(tag_type, tag_name);
            CREATE TABLE IF NOT EXISTS twse_daily_valuation (
                data_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                close_price REAL,
                dividend_yield REAL,
                dividend_year TEXT,
                pe_ratio REAL,
                pb_ratio REAL,
                financial_year_quarter TEXT,
                source TEXT NOT NULL DEFAULT 'TWSE_BWIBBU',
                source_status TEXT,
                updated_at TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'Asia/Taipei',
                PRIMARY KEY(data_date, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_twse_daily_valuation_date
                ON twse_daily_valuation(data_date);
            CREATE INDEX IF NOT EXISTS idx_twse_daily_valuation_symbol
                ON twse_daily_valuation(symbol);
            CREATE TABLE IF NOT EXISTS history_price (
                date TEXT,
                code TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                amount REAL,
                volume_unit TEXT,
                source TEXT,
                updated_at REAL,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS stock_no_trade_dates (
                trade_date TEXT NOT NULL,
                code TEXT NOT NULL,
                market TEXT NOT NULL,
                reason TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                source_quality TEXT NOT NULL DEFAULT 'official',
                evidence_json TEXT,
                verified_at REAL NOT NULL,
                PRIMARY KEY(trade_date, code)
            );
            CREATE INDEX IF NOT EXISTS idx_stock_no_trade_dates_code_date
                ON stock_no_trade_dates(code, trade_date DESC);
            CREATE TABLE IF NOT EXISTS institution_daily (
                date TEXT,
                code TEXT,
                foreign_net REAL,
                trust_net REAL,
                dealer_net REAL,
                source TEXT,
                updated_at REAL,
                source_quality TEXT,
                fetched_at REAL,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS margin_daily (
                date TEXT,
                code TEXT,
                margin_delta REAL,
                margin_balance REAL,
                short_delta REAL,
                short_balance REAL,
                source TEXT,
                updated_at REAL,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS lending_daily (
                date TEXT,
                code TEXT,
                lending_delta REAL,
                lending_balance REAL,
                source TEXT,
                updated_at REAL,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS fetch_status (
                key TEXT PRIMARY KEY,
                status TEXT,
                message TEXT,
                updated_at REAL
            );
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
            );
            CREATE TABLE IF NOT EXISTS foreign_shareholding (
                date TEXT,
                code TEXT,
                ForeignInvestmentShares REAL,
                source TEXT,
                updated_at REAL,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS stock_state_history (
                code TEXT,
                calc_date TEXT,
                calc_ts REAL,
                main_status TEXT,
                status_level INTEGER,
                display_signal TEXT,
                close REAL,
                support_text TEXT,
                resistance_text TEXT,
                reasons_json TEXT,
                PRIMARY KEY(code, calc_date)
            );
            CREATE TABLE IF NOT EXISTS mis_quote_snapshot (
                snapshot_ts TEXT,
                snapshot_date TEXT,
                code TEXT,
                price REAL,
                change_pct REAL,
                cumulative_volume REAL,
                volume_delta_since_last_poll REAL,
                trade_time TEXT,
                fetched_at TEXT,
                quote_source TEXT,
                is_estimated_tick_volume INTEGER,
                PRIMARY KEY(code, snapshot_ts)
            );
            CREATE INDEX IF NOT EXISTS idx_mis_quote_snapshot_date
                ON mis_quote_snapshot(snapshot_date);
            CREATE TABLE IF NOT EXISTS price_volume_profile_daily (
                date TEXT,
                code TEXT,
                source_level INTEGER,
                source_name TEXT,
                source_hash TEXT,
                trade_scope TEXT,
                volume_unit TEXT,
                total_volume_shares REAL,
                eod_volume_shares REAL,
                volume_diff_pct REAL,
                price_level_count INTEGER,
                min_price REAL,
                max_price REAL,
                profile_json TEXT,
                quality TEXT,
                quality_reason TEXT,
                fetched_at REAL,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS price_volume_score_daily (
                date TEXT,
                code TEXT,
                system_version TEXT,
                close REAL,
                source_name TEXT,
                source_level INTEGER,
                source_hash TEXT,
                coverage_days INTEGER,
                required_days INTEGER,
                quality TEXT,
                quality_reason TEXT,
                status TEXT,
                main_peak_price REAL,
                main_peak_distance_pct REAL,
                overhead_pressure_pct REAL,
                top3_concentration_pct REAL,
                recent_peak_strength_pct REAL,
                behavior_state TEXT,
                weighted_cost REAL,
                cost_state TEXT,
                position_score INTEGER,
                pressure_score INTEGER,
                concentration_score INTEGER,
                recent_peak_score INTEGER,
                behavior_score INTEGER,
                total_score INTEGER,
                grade TEXT,
                support_json TEXT,
                resistance_json TEXT,
                event_types TEXT,
                created_at REAL,
                PRIMARY KEY(date, code)
            );
            CREATE TABLE IF NOT EXISTS price_volume_distribution (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                price REAL NOT NULL,
                volume_lots INTEGER NOT NULL DEFAULT 0,
                volume_shares INTEGER,
                total_volume_lots INTEGER,
                snapshot_time TEXT,
                created_at REAL,
                updated_at REAL,
                UNIQUE(stock_id, trade_date, price)
            );
            CREATE INDEX IF NOT EXISTS idx_price_volume_distribution_stock
                ON price_volume_distribution(stock_id);
            CREATE INDEX IF NOT EXISTS idx_price_volume_distribution_date
                ON price_volume_distribution(trade_date);
            CREATE INDEX IF NOT EXISTS idx_price_volume_distribution_stock_date
                ON price_volume_distribution(stock_id, trade_date);
            CREATE TABLE IF NOT EXISTS taiwan50_close_batch_runs (
                data_date TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                timezone TEXT NOT NULL DEFAULT 'Asia/Taipei',
                update_mode TEXT NOT NULL DEFAULT 'close_batch',
                is_realtime INTEGER NOT NULL DEFAULT 0,
                item_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                source_status TEXT,
                reason TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS taiwan50_close_batch_items (
                data_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT,
                rank_no INTEGER,
                close_price REAL,
                reference_price REAL,
                support_zone TEXT,
                pressure_zone TEXT,
                poc_price REAL,
                poc_volume INTEGER,
                source_status TEXT,
                data_quality TEXT,
                reason TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(data_date, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_taiwan50_close_batch_items_date
                ON taiwan50_close_batch_items(data_date);
            CREATE INDEX IF NOT EXISTS idx_taiwan50_close_batch_items_symbol
                ON taiwan50_close_batch_items(symbol);
            CREATE TABLE IF NOT EXISTS taiwan50_close_volume_profile_points (
                data_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                volume INTEGER NOT NULL,
                source TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(data_date, symbol, price)
            );
            CREATE INDEX IF NOT EXISTS idx_taiwan50_close_volume_profile_points_date
                ON taiwan50_close_volume_profile_points(data_date);
            CREATE INDEX IF NOT EXISTS idx_taiwan50_close_volume_profile_points_symbol
                ON taiwan50_close_volume_profile_points(symbol);
            CREATE TABLE IF NOT EXISTS tdcc_holding_distribution (
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                level TEXT NOT NULL,
                holders INTEGER,
                shares REAL,
                percent REAL,
                source TEXT,
                updated_at REAL,
                PRIMARY KEY (date, code, level)
            );
            CREATE INDEX IF NOT EXISTS idx_tdcc_holding_distribution_code_date
                ON tdcc_holding_distribution(code, date DESC);
            CREATE TABLE IF NOT EXISTS tdcc_equity_summary (
                date TEXT NOT NULL,
                code TEXT NOT NULL,
                total_holders INTEGER,
                total_shares REAL,
                small_10_share_pct REAL,
                big_400_share_pct REAL,
                big_1000_share_pct REAL,
                small_10_change_4w REAL,
                big_400_change_4w REAL,
                big_1000_change_4w REAL,
                holder_count_change_4w REAL,
                holder_count_change_4w_pct REAL,
                equity_score REAL,
                equity_label TEXT,
                equity_reason TEXT,
                source TEXT,
                quality TEXT,
                updated_at REAL,
                PRIMARY KEY (date, code)
            );
            CREATE INDEX IF NOT EXISTS idx_tdcc_equity_summary_code_date
                ON tdcc_equity_summary(code, date DESC);
            CREATE TABLE IF NOT EXISTS intraday_quote_snapshot (
                stock_id TEXT PRIMARY KEY,
                quote_time TEXT,
                price REAL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                turnover REAL,
                vwap REAL,
                change REAL,
                change_pct REAL,
                source TEXT,
                last_updated_phase TEXT,
                data_status TEXT,
                raw_json TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS intraday_quote_1m (
                trade_date TEXT NOT NULL,
                minute_ts TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                turnover REAL,
                vwap REAL,
                source TEXT,
                data_status TEXT,
                updated_at TEXT,
                UNIQUE(trade_date, minute_ts, stock_id)
            );
            CREATE INDEX IF NOT EXISTS idx_intraday_quote_1m_stock_date
                ON intraday_quote_1m(stock_id, trade_date);
            CREATE TABLE IF NOT EXISTS daily_chip_momentum (
                date TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                close REAL,
                previous_close REAL,
                volume INTEGER,
                volume_ratio_5d REAL,
                volume_ratio_20d REAL,
                foreign_net INTEGER,
                trust_net INTEGER,
                dealer_net INTEGER,
                inst_total_net INTEGER,
                margin_balance INTEGER,
                short_balance INTEGER,
                margin_change INTEGER,
                short_change INTEGER,
                intraday_high REAL,
                intraday_low REAL,
                intraday_vwap REAL,
                chip_score INTEGER,
                chip_label TEXT,
                chip_summary TEXT,
                tdcc_trend TEXT,
                tdcc_whale_change_pct REAL,
                tdcc_shareholder_change INTEGER,
                last_updated_phase TEXT,
                data_status TEXT,
                completeness_json TEXT,
                source_flags_json TEXT,
                price_source TEXT,
                inst_source TEXT,
                margin_source TEXT,
                intraday_source TEXT,
                updated_at TEXT,
                UNIQUE(date, stock_id)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_chip_momentum_stock
                ON daily_chip_momentum(stock_id);
            CREATE TABLE IF NOT EXISTS daily_inner_outer_volume (
                stock_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                inner_volume INTEGER,
                outer_volume INTEGER,
                total_volume INTEGER,
                total_volume_check INTEGER,
                inner_ratio REAL,
                outer_ratio REAL,
                inner_outer_diff INTEGER,
                close_price REAL,
                prev_close_price REAL,
                price_change REAL,
                price_change_pct REAL,
                turnover_rate REAL,
                volume_ma5 REAL,
                volume_ma20 REAL,
                rsi14 REAL,
                chip_concentration REAL,
                chip_concentration_change REAL,
                signal_status TEXT,
                signal_strength TEXT,
                data_quality TEXT,
                source TEXT,
                updated_at TEXT,
                created_at TEXT,
                PRIMARY KEY(stock_code, trade_date)
            );
            CREATE TABLE IF NOT EXISTS next_day_outlook_daily (
                calc_date TEXT,
                code TEXT,
                generated_at REAL,
                model_version TEXT,
                probability_up REAL,
                opening_probability REAL,
                sustainability_probability REAL,
                label TEXT,
                opening_label TEXT,
                sustainability_label TEXT,
                confidence TEXT,
                consistency TEXT,
                total_score REAL,
                us_score REAL,
                night_score REAL,
                chip_score REAL,
                tech_score REAL,
                warnings_json TEXT,
                sources_json TEXT,
                payload_json TEXT,
                actual_open_change REAL,
                actual_close_change REAL,
                resolved_at REAL,
                PRIMARY KEY(calc_date, code, model_version)
            );
            """
        )
        conn.executescript(AUTH_CREATE_TABLES_SQL)
        # v2.42 migration: older DBs may still have valuation without EPS columns.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(valuation)").fetchall()}
        if "eps" not in cols:
            conn.execute("ALTER TABLE valuation ADD COLUMN eps REAL")
        if "eps_source" not in cols:
            conn.execute("ALTER TABLE valuation ADD COLUMN eps_source TEXT")
        hist_cols = {r[1] for r in conn.execute("PRAGMA table_info(history_price)").fetchall()}
        if "amount" not in hist_cols:
            conn.execute("ALTER TABLE history_price ADD COLUMN amount REAL")
        if "volume_unit" not in hist_cols:
            conn.execute("ALTER TABLE history_price ADD COLUMN volume_unit TEXT")
        conn.execute("""
            UPDATE history_price
            SET volume_unit='shares'
            WHERE (volume_unit IS NULL OR volume_unit='')
              AND (
                source LIKE 'TWSE OpenAPI%'
                OR source LIKE 'FinMind%'
                OR source LIKE 'Yahoo Finance%'
              )
        """)
        ensure_market_analytics_schema(conn)
        ensure_full_market_batch_schema(conn)
        ensure_news_radar_schema(conn)
        ensure_credit_balance_schema(conn)
        ensure_external_event_schema(conn)
        ensure_global_market_schema(conn)
        ensure_institution_activity_schema(conn)
        ensure_official_event_schema(conn)
        ensure_provenance_schema(conn)
        ensure_taifex_night_schema(conn)
        ensure_trading_restriction_schema(conn)
        ensure_company_size_schema(conn)
        ensure_single_track_v3_schema(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_schema_state(
                singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                schema_version TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO app_schema_state(singleton_id,schema_version,applied_at)
            VALUES(1,?,CURRENT_TIMESTAMP)
            ON CONFLICT(singleton_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                applied_at=excluded.applied_at
            """,
            (RUNTIME_SCHEMA_VERSION,),
        )
        conn.commit()

