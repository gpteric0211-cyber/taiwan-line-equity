from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from typing import Any

from core.db import db
from core.utils import now_tpe
from repository.full_market_batch_repository import resolve_full_market_analysis_date


DAILY_INNER_OUTER_KEEP_ROWS = 600

DAILY_INNER_OUTER_COLUMNS = [
    "stock_code",
    "trade_date",
    "inner_volume",
    "outer_volume",
    "total_volume",
    "total_volume_check",
    "inner_ratio",
    "outer_ratio",
    "inner_outer_diff",
    "close_price",
    "prev_close_price",
    "price_change",
    "price_change_pct",
    "turnover_rate",
    "volume_ma5",
    "volume_ma20",
    "rsi14",
    "chip_concentration",
    "chip_concentration_change",
    "signal_status",
    "signal_strength",
    "data_quality",
    "source",
    "updated_at",
    "created_at",
]


def normalize_stock_code(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text.isdigit() and len(text) < 4:
        text = text.zfill(4)
    return text if text.isdigit() and len(text) == 4 else None


def _now_text() -> str:
    return now_tpe().strftime("%Y-%m-%d %H:%M:%S")


def ensure_daily_inner_outer_volume_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
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
        )
        """
    )
def upsert_daily_inner_outer_volume(row: dict[str, Any], *, dry_run: bool = False) -> bool:
    code = normalize_stock_code(row.get("stock_code") or row.get("code"))
    trade_date = str(row.get("trade_date") or row.get("date") or "").strip()
    if not (code and trade_date):
        return False
    if dry_run:
        return True
    now_text = _now_text()
    normalized = {key: row.get(key) for key in DAILY_INNER_OUTER_COLUMNS}
    normalized["stock_code"] = code
    normalized["trade_date"] = trade_date
    normalized["updated_at"] = normalized.get("updated_at") or now_text
    normalized["created_at"] = normalized.get("created_at") or now_text
    columns = ",".join(DAILY_INNER_OUTER_COLUMNS)
    placeholders = ",".join("?" for _ in DAILY_INNER_OUTER_COLUMNS)
    with closing(db()) as conn:
        ensure_daily_inner_outer_volume_schema(conn)
        conn.execute(
            f"INSERT OR REPLACE INTO daily_inner_outer_volume ({columns}) VALUES ({placeholders})",
            [normalized.get(key) for key in DAILY_INNER_OUTER_COLUMNS],
        )
        conn.commit()
    return True


def latest_daily_inner_outer_volume(stock_code: str, trade_date: str | None = None) -> dict[str, Any] | None:
    code = normalize_stock_code(stock_code)
    if not code:
        return None
    with closing(db()) as conn:
        ensure_daily_inner_outer_volume_schema(conn)
        analysis_as_of = resolve_full_market_analysis_date(conn, trade_date)
        if analysis_as_of:
            row = conn.execute(
                """
                SELECT * FROM daily_inner_outer_volume
                WHERE stock_code=? AND trade_date<=?
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (code, analysis_as_of),
            ).fetchone()
        else:
            row = None
    return dict(row) if row else None


def recent_daily_inner_outer_volume(stock_code: str, *, limit: int = 20) -> list[dict[str, Any]]:
    code = normalize_stock_code(stock_code)
    if not code:
        return []
    with closing(db()) as conn:
        ensure_daily_inner_outer_volume_schema(conn)
        analysis_as_of = resolve_full_market_analysis_date(conn)
        if not analysis_as_of:
            return []
        rows = conn.execute(
            """
            SELECT * FROM daily_inner_outer_volume
            WHERE stock_code=? AND trade_date<=?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (code, analysis_as_of, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def cleanup_daily_inner_outer_volume_history(
    stock_code: str,
    *,
    keep_rows: int = DAILY_INNER_OUTER_KEEP_ROWS,
    dry_run: bool = False,
) -> int:
    code = normalize_stock_code(stock_code)
    if not code:
        return 0
    try:
        with closing(db()) as conn:
            ensure_daily_inner_outer_volume_schema(conn)
            kept = [
                row["trade_date"]
                for row in conn.execute(
                    """
                    SELECT trade_date FROM daily_inner_outer_volume
                    WHERE stock_code=?
                    ORDER BY trade_date DESC
                    LIMIT ?
                    """,
                    (code, int(keep_rows)),
                ).fetchall()
            ]
            if not kept:
                return 0
            if dry_run:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM daily_inner_outer_volume WHERE stock_code=?",
                    (code,),
                ).fetchone()
                return max(0, int(row["c"] or 0) - len(kept))
            deleted = conn.execute(
                "DELETE FROM daily_inner_outer_volume WHERE stock_code=? AND trade_date NOT IN (%s)"
                % ",".join("?" for _ in kept),
                [code, *kept],
            ).rowcount
            conn.commit()
            return int(deleted or 0)
    except sqlite3.Error:
        logging.warning("daily_inner_outer_volume cleanup failed for %s", code, exc_info=True)
        return 0


def row_to_jsonable(row: dict[str, Any] | None) -> str:
    return json.dumps(row or {}, ensure_ascii=False, sort_keys=True, default=str)
