from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from core.data_quality import assess_daily_ohlcv, verified_no_trade_evidence
from core.market_session import is_taiwan_trading_day
from core.provenance_schema import ensure_provenance_schema, record_validated_daily_ohlcv


DAILY_OHLCV_TABLE = "history_price"
MARKET_FOUNDATION_RETENTION_TRADING_DAYS = 600
AUDIT_RETENTION_DAYS = 730

SOURCE_PRIORITY = {
    "TWSE_OFFICIAL": 100,
    "TPEX_OFFICIAL": 100,
    "TWSE OpenAPI": 100,
    # STOCK_DAY carries an explicit official trading date and is preferred over
    # the undated all-market snapshot endpoint.
    "TWSE STOCK_DAY": 120,
    "TWSE MI_INDEX": 120,
    "TPEX OpenAPI": 100,
    "TPEX TRADING_STOCK": 120,
    "TPEX DAILY_QUOTES": 120,
    "FINMIND": 50,
    "FinMind": 50,
    "YAHOO": 10,
    "Yahoo Finance": 10,
    "PCHOME": 10,
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
    if not _table_exists(conn, table):
        return False
    cols = _table_columns(conn, table)
    if column in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return True


def source_rank(source: Any) -> int:
    text = str(source or "").strip()
    if text in SOURCE_PRIORITY:
        return SOURCE_PRIORITY[text]
    upper = text.upper()
    if "TWSE" in upper and ("OFFICIAL" in upper or "OPENAPI" in upper or "STOCK_DAY" in upper or "MI_INDEX" in upper):
        return 100
    if "TPEX" in upper and ("OFFICIAL" in upper or "OPENAPI" in upper or "TRADING_STOCK" in upper or "DAILY_QUOTES" in upper):
        return 100
    if "FINMIND" in upper:
        return 50
    if "YAHOO" in upper or "PCHOME" in upper:
        return 10
    return 0


def resolve_daily_ohlcv_table(conn: sqlite3.Connection) -> str:
    cols = _table_columns(conn, "history_price")
    required = {"date", "code", "open", "high", "low", "close", "volume"}
    if required.issubset(cols):
        return "history_price"
    raise RuntimeError("Cannot safely resolve daily OHLCV table: history_price lacks required OHLCV columns")


def ensure_market_foundation_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    daily_table = resolve_daily_ohlcv_table(conn)
    added: dict[str, list[str]] = {}

    for table, columns in {
        "history_price": {
            "source_quality": "TEXT",
            "fetched_at": "REAL",
            "market": "TEXT",
        },
        "institution_daily": {
            "source_quality": "TEXT",
            "fetched_at": "REAL",
        },
        "margin_daily": {
            "source_quality": "TEXT",
            "fetched_at": "REAL",
        },
        "tdcc_equity_summary": {
            "source_quality": "TEXT",
            "fetched_at": "REAL",
        },
        "price_volume_distribution": {
            "buy_volume_lots": "INTEGER NOT NULL DEFAULT 0",
            "sell_volume_lots": "INTEGER NOT NULL DEFAULT 0",
            "neutral_volume_lots": "INTEGER NOT NULL DEFAULT 0",
            "source": "TEXT",
            "source_quality": "TEXT",
            "fetched_at": "REAL",
        },
    }.items():
        for col, definition in columns.items():
            if _add_column_if_missing(conn, table, col, definition):
                added.setdefault(table, []).append(col)

    # One-time/idempotent metadata normalization for legacy rows.  This does
    # not alter prices; it makes source trust explicit for readiness checks.
    conn.execute(
        """
        UPDATE history_price
        SET source_quality = CASE
            WHEN source LIKE 'TWSE%' OR source LIKE 'TPEX%' THEN 'official'
            WHEN source LIKE 'FinMind%' OR source LIKE 'Yahoo Finance%' THEN 'fallback'
            ELSE 'unknown'
        END
        WHERE source_quality IS NULL OR TRIM(source_quality)=''
        """
    )
    if _table_exists(conn, "stock_industry_profile"):
        conn.execute(
            """
            UPDATE history_price
            SET market = CASE (
                SELECT market FROM stock_industry_profile profile
                WHERE profile.code=history_price.code
            )
                WHEN '上市' THEN 'listed'
                WHEN '上櫃' THEN 'otc'
                ELSE (
                    SELECT market FROM stock_industry_profile profile
                    WHERE profile.code=history_price.code
                )
            END
            WHERE (market IS NULL OR TRIM(market)='')
              AND EXISTS(
                SELECT 1 FROM stock_industry_profile profile
                WHERE profile.code=history_price.code
                  AND profile.market IS NOT NULL
                  AND TRIM(profile.market)<>''
              )
            """
        )
    conn.execute(
        """
        UPDATE history_price
        SET fetched_at=updated_at
        WHERE fetched_at IS NULL AND updated_at IS NOT NULL
        """
    )
    conn.execute(
        """
        UPDATE history_price
        SET market = CASE
            WHEN source LIKE 'TWSE%' OR source LIKE '%.TW' THEN 'listed'
            WHEN source LIKE 'TPEX%' OR source LIKE '%.TWO' THEN 'otc'
            ELSE market
        END
        WHERE market IS NULL OR TRIM(market)=''
        """
    )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS intraday_time_sales_daily (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            trade_time TEXT NOT NULL,
            price REAL NOT NULL,
            volume_lots INTEGER NOT NULL DEFAULT 0,
            side TEXT NOT NULL DEFAULT 'UNKNOWN',
            source TEXT NOT NULL,
            source_quality TEXT,
            fetched_at REAL,
            raw_json TEXT,
            PRIMARY KEY(code, trade_date, trade_time, price, source)
        );
        CREATE INDEX IF NOT EXISTS idx_intraday_time_sales_daily_code_date
            ON intraday_time_sales_daily(code, trade_date);
        CREATE TABLE IF NOT EXISTS daily_data_source_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            run_date TEXT,
            mode TEXT,
            code TEXT,
            source TEXT,
            status TEXT NOT NULL,
            message TEXT,
            rows_read INTEGER DEFAULT 0,
            rows_written INTEGER DEFAULT 0,
            payload_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_daily_data_source_audit_started_at
            ON daily_data_source_audit(started_at);
        CREATE INDEX IF NOT EXISTS idx_daily_data_source_audit_source_status
            ON daily_data_source_audit(source, status);
        """
    )
    ensure_provenance_schema(conn)
    for table, index_name in (
        ("history_price", "idx_history_price_code_date"),
        ("institution_daily", "idx_institution_daily_code_date"),
        ("margin_daily", "idx_margin_daily_code_date"),
    ):
        if _table_exists(conn, table):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}(code, date)"
            )
    conn.commit()
    return {"daily_ohlcv_table": daily_table, "added_columns": added}


def upsert_daily_ohlcv(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    ensure_market_foundation_schema(conn)
    return _upsert_daily_ohlcv_without_schema_check(conn, row)


def _upsert_daily_ohlcv_without_schema_check(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "").strip()
    new_rank = source_rank(source)
    source_quality = str(
        row.get("source_quality")
        or ("OK" if new_rank >= 100 else "FALLBACK")
    ).strip()
    values = {
        "date": row.get("date"),
        "code": str(row.get("code") or "").zfill(4),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("volume"),
        "amount": row.get("amount"),
        "volume_unit": row.get("volume_unit") or "shares",
        "source": source,
        "updated_at": row.get("updated_at"),
        "source_quality": source_quality,
        "fetched_at": row.get("fetched_at"),
        "market": row.get("market"),
    }
    validation = assess_daily_ohlcv(values)
    if not validation["ready"]:
        return False
    values["date"] = validation["date"]
    values["code"] = validation["code"]
    no_trade = verified_no_trade_evidence(conn, values["code"], values["date"])
    if no_trade:
        # A fallback source can never create a bar on an exchange-verified
        # no-trade date.  A later corrected official row may replace the
        # evidence only when it comes from the same market/source and carries
        # a strictly newer fetch timestamp.
        try:
            incoming_fetched_at = float(values.get("fetched_at") or 0)
            evidence_verified_at = float(no_trade.get("verified_at") or 0)
        except (TypeError, ValueError):
            incoming_fetched_at = 0
            evidence_verified_at = 0
        same_official_source = bool(
            new_rank >= 100
            and source == str(no_trade.get("source") or "")
            and str(values.get("market") or "").lower() == str(no_trade.get("market") or "").lower()
        )
        if not same_official_source or incoming_fetched_at <= evidence_verified_at:
            return False
        conn.execute(
            "DELETE FROM stock_no_trade_dates WHERE code=? AND trade_date=?",
            (values["code"], values["date"]),
        )
    if not is_taiwan_trading_day(datetime.fromisoformat(str(values["date"])).date()):
        return False

    old = conn.execute(
        "SELECT source FROM history_price WHERE date=? AND code=?",
        (values["date"], values["code"]),
    ).fetchone()
    if old and source_rank(old[0]) > new_rank:
        return False

    conn.execute(
        """
        INSERT INTO history_price(
            date, code, open, high, low, close, volume, amount, volume_unit,
            source, updated_at, source_quality, fetched_at, market
        ) VALUES(
            :date, :code, :open, :high, :low, :close, :volume, :amount, :volume_unit,
            :source, :updated_at, :source_quality, :fetched_at, :market
        )
        ON CONFLICT(date, code) DO UPDATE SET
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            amount=excluded.amount,
            volume_unit=excluded.volume_unit,
            source=excluded.source,
            updated_at=excluded.updated_at,
            source_quality=excluded.source_quality,
            fetched_at=excluded.fetched_at,
            market=excluded.market
        """,
        values,
    )
    record_validated_daily_ohlcv(conn, values)
    return True


def upsert_daily_ohlcv_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    ensure_schema: bool = True,
) -> int:
    """Bulk UPSERT normalized OHLCV rows while preserving source priority."""

    if ensure_schema:
        ensure_market_foundation_schema(conn)
    written = 0
    for row in rows:
        if _upsert_daily_ohlcv_without_schema_check(conn, row):
            written += 1
    return written


def upsert_price_volume_distribution(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    ensure_market_foundation_schema(conn)
    stock_id = str(row.get("code") or row.get("stock_id") or "").strip().zfill(4)
    trade_date = str(row.get("trade_date") or "").strip()
    price = row.get("price")
    if not stock_id or not trade_date or price is None:
        return False
    values = {
        "stock_id": stock_id,
        "trade_date": trade_date,
        "price": price,
        "volume_lots": int(row.get("volume_lots") or 0),
        "buy_volume_lots": int(row.get("buy_volume_lots") or 0),
        "sell_volume_lots": int(row.get("sell_volume_lots") or 0),
        "neutral_volume_lots": int(row.get("neutral_volume_lots") or 0),
        "source": row.get("source") or "UNKNOWN",
        "source_quality": row.get("source_quality") or "PARTIAL",
        "fetched_at": row.get("fetched_at"),
    }
    conn.execute(
        """
        INSERT INTO price_volume_distribution(
            stock_id, trade_date, price, volume_lots, buy_volume_lots,
            sell_volume_lots, neutral_volume_lots, source, source_quality,
            fetched_at, created_at, updated_at
        ) VALUES(
            :stock_id, :trade_date, :price, :volume_lots, :buy_volume_lots,
            :sell_volume_lots, :neutral_volume_lots, :source, :source_quality,
            :fetched_at, :fetched_at, :fetched_at
        )
        ON CONFLICT(stock_id, trade_date, price) DO UPDATE SET
            volume_lots=excluded.volume_lots,
            buy_volume_lots=excluded.buy_volume_lots,
            sell_volume_lots=excluded.sell_volume_lots,
            neutral_volume_lots=excluded.neutral_volume_lots,
            source=excluded.source,
            source_quality=excluded.source_quality,
            fetched_at=excluded.fetched_at,
            updated_at=excluded.fetched_at
        """,
        values,
    )
    return True


def record_data_source_audit(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    ensure_market_foundation_schema(conn)
    conn.execute(
        """
        INSERT INTO daily_data_source_audit(
            started_at, finished_at, run_date, mode, code, source, status,
            message, rows_read, rows_written, payload_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload.get("started_at"),
            payload.get("finished_at"),
            payload.get("run_date"),
            payload.get("mode"),
            payload.get("code"),
            payload.get("source"),
            payload.get("status") or "UNKNOWN",
            payload.get("message"),
            int(payload.get("rows_read") or 0),
            int(payload.get("rows_written") or 0),
            payload.get("payload_json"),
        ),
    )


def _delete_older_than(conn: sqlite3.Connection, table: str, date_col: str, cutoff: str, batch_size: int = 5000) -> int:
    deleted = 0
    while True:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {date_col} < ? LIMIT ?)",
            (cutoff, batch_size),
        )
        count = int(cur.rowcount or 0)
        deleted += count
        if count < batch_size:
            break
    return deleted


def prune_market_foundation_data(conn: sqlite3.Connection, retain_trading_days: int = MARKET_FOUNDATION_RETENTION_TRADING_DAYS) -> dict[str, Any]:
    ensure_market_foundation_schema(conn)
    dates = [
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT date FROM history_price WHERE date IS NOT NULL AND date<>'' ORDER BY date DESC LIMIT ?",
            (retain_trading_days,),
        ).fetchall()
    ]
    if len(dates) < retain_trading_days:
        cutoff = dates[-1] if dates else None
        deleted: dict[str, int] = {}
    else:
        cutoff = dates[-1]
        deleted = {}
        for table, date_col in [
            ("history_price", "date"),
            ("institution_daily", "date"),
            ("margin_daily", "date"),
            ("price_volume_profile_daily", "date"),
            ("price_volume_score_daily", "date"),
            ("price_volume_distribution", "trade_date"),
            ("intraday_time_sales_daily", "trade_date"),
            ("daily_chip_momentum", "date"),
            ("daily_inner_outer_volume", "trade_date"),
            ("intraday_quote_1m", "trade_date"),
            ("tdcc_equity_summary", "date"),
        ]:
            if _table_exists(conn, table) and date_col in _table_columns(conn, table):
                deleted[table] = _delete_older_than(conn, table, date_col, cutoff)

    audit_cutoff = (datetime.now() - timedelta(days=AUDIT_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    audit_deleted = 0
    if _table_exists(conn, "daily_data_source_audit"):
        audit_deleted = _delete_older_than(conn, "daily_data_source_audit", "started_at", audit_cutoff)
    conn.execute("PRAGMA optimize")
    conn.commit()
    return {
        "retain_trading_days": retain_trading_days,
        "cutoff_trade_date": cutoff,
        "deleted": deleted,
        "audit_cutoff": audit_cutoff,
        "audit_deleted": audit_deleted,
        "vacuum": "not_run",
    }
