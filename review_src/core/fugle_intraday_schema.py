from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


FUGLE_SOURCE = "FUGLE"
FUGLE_INTRADAY_RETAIN_DAYS = 300
FUGLE_BID_ASK_MAPPING_NOTE = (
    "Fugle stock intraday-volumes documentation defines volumeAtBid as source-calculated "
    "inner volume and volumeAtAsk as source-calculated outer volume. The opening auction's "
    "first trade may be excluded, and these fields are not institutional net buy/sell."
)
FUGLE_TRADE_SIDE_NOTE = (
    "side_inferred is inferred from bid/ask first, then a tick rule fallback. "
    "It is supplemental and must not be treated as an exchange-original side."
)
SIDE_LABELS_ZH = {
    "ASK": "外盤",
    "BID": "內盤",
    "MID": "中性",
    "UNKNOWN": "無法判斷",
    "INVALID_QUOTE": "報價異常",
}
VALID_SIDE_VALUES = set(SIDE_LABELS_ZH)
VALID_SIDE_METHODS = {"PRICE_VS_BID_ASK", "PRICE_TICK_FROM_PREV_TRADE", "UNKNOWN"}
VALID_SIDE_CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
VALID_PREV_PRICE_SOURCES = {"PREVIOUS_CLOSE", "PREVIOUS_TRADE", "NOT_USED", "UNKNOWN"}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
    if not _table_exists(conn, table):
        return False
    if column in _table_columns(conn, table):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return True


def ensure_fugle_intraday_schema(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Create/extend only Fugle supplemental intraday storage.

    The official all-market `history_price` flow is intentionally untouched.
    """
    added: dict[str, list[str]] = {}
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fugle_intraday_trades (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            trade_time TEXT NOT NULL,
            price REAL,
            size INTEGER,
            volume INTEGER,
            bid REAL,
            ask REAL,
            serial TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'FUGLE',
            fetched_at REAL,
            data_quality TEXT,
            side_inferred TEXT,
            side_label_zh TEXT,
            side_method TEXT,
            side_confidence TEXT,
            side_reason TEXT,
            prev_price REAL,
            prev_price_source TEXT,
            raw_json TEXT,
            PRIMARY KEY(code, trade_date, serial, source)
        );
        CREATE INDEX IF NOT EXISTS idx_fugle_intraday_trades_code_date
            ON fugle_intraday_trades(code, trade_date);
        CREATE TABLE IF NOT EXISTS fugle_intraday_capture_runs (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'FUGLE',
            snapshot_time TEXT NOT NULL,
            page_count INTEGER NOT NULL DEFAULT 0,
            provider_row_count INTEGER NOT NULL DEFAULT 0,
            normalized_row_count INTEGER NOT NULL DEFAULT 0,
            stored_row_count INTEGER NOT NULL DEFAULT 0,
            capture_complete INTEGER NOT NULL DEFAULT 0,
            data_quality TEXT NOT NULL,
            reason TEXT,
            latest_trade_time TEXT,
            latest_cumulative_volume INTEGER,
            captured_volume_lots INTEGER,
            fetched_at REAL,
            PRIMARY KEY(code, trade_date, endpoint, source)
        );
        CREATE INDEX IF NOT EXISTS idx_fugle_capture_runs_date
            ON fugle_intraday_capture_runs(trade_date, endpoint, capture_complete);
        """
    )
    for column, definition in {
        "side_inferred": "TEXT",
        "side_label_zh": "TEXT",
        "side_method": "TEXT",
        "side_confidence": "TEXT",
        "side_reason": "TEXT",
        "prev_price": "REAL",
        "prev_price_source": "TEXT",
    }.items():
        if _add_column_if_missing(conn, "fugle_intraday_trades", column, definition):
            added.setdefault("fugle_intraday_trades", []).append(column)
    for table, columns in {
        "price_volume_distribution": {
            "volume_at_bid": "INTEGER",
            "volume_at_ask": "INTEGER",
            "neutral_volume_lots": "INTEGER NOT NULL DEFAULT 0",
            "source": "TEXT",
            "data_quality": "TEXT",
            "source_quality": "TEXT",
            "fetched_at": "REAL",
            "snapshot_time": "TEXT",
        },
        "daily_inner_outer_volume": {
            "bid_volume": "INTEGER",
            "ask_volume": "INTEGER",
            "neutral_volume": "INTEGER",
            "mapping_note": "TEXT",
            "fetched_at": "REAL",
        },
        "fugle_intraday_capture_runs": {
            "captured_volume_lots": "INTEGER",
        },
    }.items():
        for column, definition in columns.items():
            if _add_column_if_missing(conn, table, column, definition):
                added.setdefault(table, []).append(column)
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_price_volume_distribution_stock_date_source
            ON price_volume_distribution(stock_id, trade_date, source);
        CREATE INDEX IF NOT EXISTS idx_daily_inner_outer_volume_source_date
            ON daily_inner_outer_volume(source, trade_date);
        """
    )
    conn.commit()
    return added


def upsert_fugle_trade(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    ensure_schema: bool = True,
) -> bool:
    if ensure_schema:
        ensure_fugle_intraday_schema(conn)
    code = str(row.get("code") or "").strip().zfill(4)
    trade_date = str(row.get("trade_date") or "").strip()
    serial = str(row.get("serial") or "").strip()
    if not code or not trade_date or not serial:
        return False
    side_inferred = str(row.get("side_inferred") or "UNKNOWN").strip().upper()
    if side_inferred not in VALID_SIDE_VALUES:
        side_inferred = "UNKNOWN"
    side_method = str(row.get("side_method") or "UNKNOWN").strip().upper()
    if side_method not in VALID_SIDE_METHODS:
        side_method = "UNKNOWN"
    side_confidence = str(row.get("side_confidence") or "UNKNOWN").strip().upper()
    if side_confidence not in VALID_SIDE_CONFIDENCE:
        side_confidence = "UNKNOWN"
    prev_price_source = str(row.get("prev_price_source") or "UNKNOWN").strip().upper()
    if prev_price_source not in VALID_PREV_PRICE_SOURCES:
        prev_price_source = "UNKNOWN"
    side_label_zh = str(row.get("side_label_zh") or SIDE_LABELS_ZH.get(side_inferred) or SIDE_LABELS_ZH["UNKNOWN"]).strip()
    side_reason = str(row.get("side_reason") or "fallback unknown").strip() or "fallback unknown"
    values = {
        "code": code,
        "trade_date": trade_date,
        "trade_time": str(row.get("trade_time") or "").strip(),
        "price": row.get("price"),
        "size": row.get("size"),
        "volume": row.get("volume"),
        "bid": row.get("bid"),
        "ask": row.get("ask"),
        "serial": serial,
        "source": FUGLE_SOURCE,
        "fetched_at": row.get("fetched_at") or time.time(),
        "data_quality": row.get("data_quality") or "PARTIAL",
        "raw_json": json.dumps(row.get("raw_json") or {}, ensure_ascii=False, separators=(",", ":")),
        "side_inferred": side_inferred,
        "side_label_zh": side_label_zh,
        "side_method": side_method,
        "side_confidence": side_confidence,
        "side_reason": side_reason,
        "prev_price": row.get("prev_price"),
        "prev_price_source": prev_price_source,
    }
    conn.execute(
        """
        INSERT INTO fugle_intraday_trades(
            code, trade_date, trade_time, price, size, volume, bid, ask,
            serial, source, fetched_at, data_quality,
            side_inferred, side_label_zh, side_method, side_confidence,
            side_reason, prev_price, prev_price_source, raw_json
        ) VALUES(
            :code, :trade_date, :trade_time, :price, :size, :volume, :bid, :ask,
            :serial, :source, :fetched_at, :data_quality,
            :side_inferred, :side_label_zh, :side_method, :side_confidence,
            :side_reason, :prev_price, :prev_price_source, :raw_json
        )
        ON CONFLICT(code, trade_date, serial, source) DO UPDATE SET
            trade_time=excluded.trade_time,
            price=excluded.price,
            size=excluded.size,
            volume=excluded.volume,
            bid=excluded.bid,
            ask=excluded.ask,
            fetched_at=excluded.fetched_at,
            data_quality=excluded.data_quality,
            side_inferred=excluded.side_inferred,
            side_label_zh=excluded.side_label_zh,
            side_method=excluded.side_method,
            side_confidence=excluded.side_confidence,
            side_reason=excluded.side_reason,
            prev_price=excluded.prev_price,
            prev_price_source=excluded.prev_price_source,
            raw_json=excluded.raw_json
        """,
        values,
    )
    return True


def upsert_fugle_capture_run(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    ensure_schema: bool = True,
) -> bool:
    if ensure_schema:
        ensure_fugle_intraday_schema(conn)
    code = str(row.get("code") or "").strip().zfill(4)
    trade_date = str(row.get("trade_date") or "").strip()
    endpoint = str(row.get("endpoint") or "").strip().lower()
    snapshot_time = str(row.get("snapshot_time") or "").strip()
    if not code or not trade_date or not endpoint or not snapshot_time:
        return False
    values = {
        "code": code,
        "trade_date": trade_date,
        "endpoint": endpoint,
        "source": FUGLE_SOURCE,
        "snapshot_time": snapshot_time,
        "page_count": max(0, int(row.get("page_count") or 0)),
        "provider_row_count": max(0, int(row.get("provider_row_count") or 0)),
        "normalized_row_count": max(0, int(row.get("normalized_row_count") or 0)),
        "stored_row_count": max(0, int(row.get("stored_row_count") or 0)),
        "capture_complete": 1 if row.get("capture_complete") else 0,
        "data_quality": str(row.get("data_quality") or "UNVERIFIED").strip().upper(),
        "reason": str(row.get("reason") or "").strip(),
        "latest_trade_time": str(row.get("latest_trade_time") or "").strip() or None,
        "latest_cumulative_volume": row.get("latest_cumulative_volume"),
        "captured_volume_lots": row.get("captured_volume_lots"),
        "fetched_at": row.get("fetched_at") or time.time(),
    }
    existing = conn.execute(
        """
        SELECT capture_complete,data_quality,normalized_row_count,stored_row_count
        FROM fugle_intraday_capture_runs
        WHERE code=? AND trade_date=? AND endpoint=? AND source=?
        """,
        (code, trade_date, endpoint, FUGLE_SOURCE),
    ).fetchone()
    if existing:
        existing_complete = bool(int(existing[0] or 0))
        existing_rows = min(int(existing[2] or 0), int(existing[3] or 0))
        incoming_complete = bool(values["capture_complete"])
        incoming_rows = min(int(values["normalized_row_count"]), int(values["stored_row_count"]))
        incoming_quality = str(values["data_quality"])
        if (
            (existing_complete and not incoming_complete)
            or (existing_rows > 0 and incoming_rows == 0 and incoming_quality in {"UNAVAILABLE", "FAILED"})
        ):
            return True
    conn.execute(
        """
        INSERT INTO fugle_intraday_capture_runs(
            code,trade_date,endpoint,source,snapshot_time,page_count,
            provider_row_count,normalized_row_count,stored_row_count,
            capture_complete,data_quality,reason,latest_trade_time,
            latest_cumulative_volume,captured_volume_lots,fetched_at
        ) VALUES(
            :code,:trade_date,:endpoint,:source,:snapshot_time,:page_count,
            :provider_row_count,:normalized_row_count,:stored_row_count,
            :capture_complete,:data_quality,:reason,:latest_trade_time,
            :latest_cumulative_volume,:captured_volume_lots,:fetched_at
        )
        ON CONFLICT(code,trade_date,endpoint,source) DO UPDATE SET
            snapshot_time=excluded.snapshot_time,
            page_count=excluded.page_count,
            provider_row_count=excluded.provider_row_count,
            normalized_row_count=excluded.normalized_row_count,
            stored_row_count=excluded.stored_row_count,
            capture_complete=excluded.capture_complete,
            data_quality=excluded.data_quality,
            reason=excluded.reason,
            latest_trade_time=excluded.latest_trade_time,
            latest_cumulative_volume=excluded.latest_cumulative_volume,
            captured_volume_lots=excluded.captured_volume_lots,
            fetched_at=excluded.fetched_at
        """,
        values,
    )
    return True


def upsert_fugle_price_volume(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    ensure_schema: bool = True,
) -> bool:
    if ensure_schema:
        ensure_fugle_intraday_schema(conn)
    code = str(row.get("code") or "").strip().zfill(4)
    trade_date = str(row.get("trade_date") or "").strip()
    price = row.get("price")
    if not code or not trade_date or price is None:
        return False
    volume = int(row.get("volume_lots") or 0)
    fetched_at = row.get("fetched_at") or time.time()
    values = {
        "stock_id": code,
        "trade_date": trade_date,
        "price": price,
        "volume_lots": volume,
        "volume_shares": volume * 1000,
        "total_volume_lots": row.get("total_volume_lots"),
        "volume_at_bid": row.get("volume_at_bid"),
        "volume_at_ask": row.get("volume_at_ask"),
        "neutral_volume_lots": row.get("neutral_volume_lots") or 0,
        "source": FUGLE_SOURCE,
        "data_quality": row.get("data_quality") or "PARTIAL",
        "source_quality": row.get("data_quality") or "PARTIAL",
        "fetched_at": fetched_at,
        "snapshot_time": row.get("snapshot_time"),
    }
    conn.execute(
        """
        INSERT INTO price_volume_distribution(
            stock_id, trade_date, price, volume_lots, volume_shares,
            total_volume_lots, volume_at_bid, volume_at_ask, neutral_volume_lots,
            source, data_quality, source_quality, snapshot_time, fetched_at, created_at, updated_at
        ) VALUES(
            :stock_id, :trade_date, :price, :volume_lots, :volume_shares,
            :total_volume_lots, :volume_at_bid, :volume_at_ask, :neutral_volume_lots,
            :source, :data_quality, :source_quality, :snapshot_time, :fetched_at, :fetched_at, :fetched_at
        )
        ON CONFLICT(stock_id, trade_date, price) DO UPDATE SET
            volume_lots=excluded.volume_lots,
            volume_shares=excluded.volume_shares,
            total_volume_lots=excluded.total_volume_lots,
            volume_at_bid=excluded.volume_at_bid,
            volume_at_ask=excluded.volume_at_ask,
            neutral_volume_lots=excluded.neutral_volume_lots,
            source=excluded.source,
            data_quality=excluded.data_quality,
            source_quality=excluded.source_quality,
            snapshot_time=excluded.snapshot_time,
            fetched_at=excluded.fetched_at,
            updated_at=excluded.fetched_at
        """,
        values,
    )
    return True


def upsert_fugle_bid_ask_summary(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    ensure_fugle_intraday_schema(conn)
    code = str(row.get("code") or "").strip().zfill(4)
    trade_date = str(row.get("trade_date") or "").strip()
    if not code or not trade_date:
        return False
    now_text = str(row.get("updated_at") or "")
    fetched_at = row.get("fetched_at") or time.time()
    values = {
        "stock_code": code,
        "trade_date": trade_date,
        "total_volume": row.get("total_volume"),
        "total_volume_check": row.get("total_volume"),
        "bid_volume": row.get("bid_volume"),
        "ask_volume": row.get("ask_volume"),
        "neutral_volume": row.get("neutral_volume"),
        "mapping_note": row.get("mapping_note") or FUGLE_BID_ASK_MAPPING_NOTE,
        "signal_status": "supplemental_bid_ask_only",
        "signal_strength": "not_scored",
        "data_quality": row.get("data_quality") or "PARTIAL",
        "source": FUGLE_SOURCE,
        "updated_at": now_text,
        "created_at": now_text,
        "fetched_at": fetched_at,
    }
    conn.execute(
        """
        INSERT INTO daily_inner_outer_volume(
            stock_code, trade_date, total_volume, total_volume_check,
            bid_volume, ask_volume, neutral_volume, mapping_note,
            signal_status, signal_strength, data_quality, source,
            updated_at, created_at, fetched_at
        ) VALUES(
            :stock_code, :trade_date, :total_volume, :total_volume_check,
            :bid_volume, :ask_volume, :neutral_volume, :mapping_note,
            :signal_status, :signal_strength, :data_quality, :source,
            :updated_at, :created_at, :fetched_at
        )
        ON CONFLICT(stock_code, trade_date) DO UPDATE SET
            total_volume=excluded.total_volume,
            total_volume_check=excluded.total_volume_check,
            bid_volume=excluded.bid_volume,
            ask_volume=excluded.ask_volume,
            neutral_volume=excluded.neutral_volume,
            mapping_note=excluded.mapping_note,
            signal_status=excluded.signal_status,
            signal_strength=excluded.signal_strength,
            data_quality=excluded.data_quality,
            source=excluded.source,
            updated_at=excluded.updated_at,
            fetched_at=excluded.fetched_at
        """,
        values,
    )
    return True


def _recent_dates(conn: sqlite3.Connection, table: str, date_col: str, retain_days: int) -> list[str]:
    if not _table_exists(conn, table):
        return []
    return [
        str(row[0])
        for row in conn.execute(
            f"""
            SELECT DISTINCT {date_col}
            FROM {table}
            WHERE {date_col} IS NOT NULL AND {date_col}<>''
            ORDER BY {date_col} DESC
            LIMIT ?
            """,
            (max(1, int(retain_days)),),
        ).fetchall()
    ]


def _delete_fugle_older_than(conn: sqlite3.Connection, table: str, date_col: str, cutoff: str) -> int:
    if not _table_exists(conn, table):
        return 0
    source_filter = "source=?" if "source" in _table_columns(conn, table) else "1=1"
    params: tuple[Any, ...]
    if source_filter == "source=?":
        params = (FUGLE_SOURCE, cutoff)
    else:
        params = (cutoff,)
    cur = conn.execute(
        f"DELETE FROM {table} WHERE {source_filter} AND {date_col} < ?",
        params,
    )
    return int(cur.rowcount or 0)


def prune_fugle_intraday_data(conn: sqlite3.Connection, retain_days: int = FUGLE_INTRADAY_RETAIN_DAYS) -> dict[str, Any]:
    ensure_fugle_intraday_schema(conn)
    retain_days = max(1, int(retain_days))
    table_specs = [
        ("fugle_intraday_trades", "trade_date"),
        ("fugle_intraday_capture_runs", "trade_date"),
        ("price_volume_distribution", "trade_date"),
        ("daily_inner_outer_volume", "trade_date"),
    ]
    cutoffs: dict[str, str | None] = {}
    deleted: dict[str, int] = {}
    for table, date_col in table_specs:
        dates = _recent_dates(conn, table, date_col, retain_days)
        cutoff = dates[-1] if len(dates) >= retain_days else None
        cutoffs[table] = cutoff
        deleted[table] = _delete_fugle_older_than(conn, table, date_col, cutoff) if cutoff else 0
    conn.commit()
    return {
        "retain_trading_days": retain_days,
        "cutoff_dates": cutoffs,
        "deleted": deleted,
    }
