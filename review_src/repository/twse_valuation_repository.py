from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from adapter.twse_valuation import normalize_symbol
from core.db import db
from core.utils import now_tpe


TPE = ZoneInfo("Asia/Taipei")


def ensure_twse_daily_valuation_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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
            available_at TEXT,
            timezone TEXT NOT NULL DEFAULT 'Asia/Taipei',
            PRIMARY KEY(data_date, symbol)
        );
        CREATE INDEX IF NOT EXISTS idx_twse_daily_valuation_date
            ON twse_daily_valuation(data_date);
        CREATE INDEX IF NOT EXISTS idx_twse_daily_valuation_symbol
            ON twse_daily_valuation(symbol);
        """
    )
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(twse_daily_valuation)").fetchall()
    }
    if "available_at" not in columns:
        conn.execute("ALTER TABLE twse_daily_valuation ADD COLUMN available_at TEXT")


def _as_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def upsert_twse_daily_valuations(
    rows: list[dict[str, Any]],
    conn: sqlite3.Connection | None = None,
) -> int:
    if not rows:
        return 0

    def _run(c: sqlite3.Connection) -> int:
        ensure_twse_daily_valuation_schema(c)
        c.executemany(
            """
            INSERT OR REPLACE INTO twse_daily_valuation(
                data_date, symbol, name, close_price, dividend_yield,
                dividend_year, pe_ratio, pb_ratio, financial_year_quarter,
                source, source_status, updated_at, available_at, timezone
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    row["data_date"],
                    normalize_symbol(row["symbol"]),
                    row.get("name"),
                    row.get("close_price"),
                    row.get("dividend_yield"),
                    row.get("dividend_year"),
                    row.get("pe_ratio"),
                    row.get("pb_ratio"),
                    row.get("financial_year_quarter"),
                    row.get("source") or "TWSE_BWIBBU",
                    row.get("source_status") or "ok",
                    row.get("updated_at") or now_tpe().strftime("%Y-%m-%d %H:%M:%S"),
                    row.get("available_at") or now_tpe().isoformat(timespec="seconds"),
                    row.get("timezone") or "Asia/Taipei",
                )
                for row in rows
                if normalize_symbol(row.get("symbol"))
            ],
        )
        return c.total_changes

    if conn is not None:
        before = conn.total_changes
        _run(conn)
        return conn.total_changes - before

    with closing(db()) as c:
        try:
            c.execute("BEGIN")
            before = c.total_changes
            _run(c)
            c.commit()
            return c.total_changes - before
        except Exception:
            c.rollback()
            raise


def get_twse_valuation(
    symbol: str,
    data_date: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    code = normalize_symbol(symbol)

    def _run(c: sqlite3.Connection) -> dict[str, Any] | None:
        ensure_twse_daily_valuation_schema(c)
        if data_date:
            row = c.execute(
                "SELECT * FROM twse_daily_valuation WHERE symbol=? AND data_date=? LIMIT 1",
                (code, data_date),
            ).fetchone()
        else:
            row = c.execute(
                """
                SELECT *
                FROM twse_daily_valuation
                WHERE symbol=?
                ORDER BY data_date DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        return _as_dict(row)

    if conn is not None:
        return _run(conn)
    with closing(db()) as c:
        return _run(c)


def get_latest_twse_valuation_date(conn: sqlite3.Connection | None = None) -> str | None:
    def _run(c: sqlite3.Connection) -> str | None:
        ensure_twse_daily_valuation_schema(c)
        row = c.execute("SELECT MAX(data_date) AS data_date FROM twse_daily_valuation").fetchone()
        return str(row["data_date"]) if row and row["data_date"] else None

    if conn is not None:
        return _run(conn)
    with closing(db()) as c:
        return _run(c)


def get_twse_valuation_at_cutoff(
    symbol: str,
    *,
    analysis_cutoff: str,
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    """Read the latest row whose actual first-observed time is visible at cutoff.

    Legacy rows with a missing or offset-naive availability value are excluded;
    the repository never guesses their publication or retrieval time.
    """

    code = normalize_symbol(symbol)
    try:
        cutoff = datetime.fromisoformat(str(analysis_cutoff).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("analysis_cutoff must be an ISO-8601 timestamp") from exc
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("analysis_cutoff must include an explicit UTC offset")
    cutoff = cutoff.astimezone(TPE)
    try:
        cursor = conn.execute(
            """
            SELECT * FROM twse_daily_valuation
            WHERE symbol=? AND available_at IS NOT NULL
            ORDER BY data_date DESC,available_at DESC
            """,
            (code,),
        )
    except sqlite3.OperationalError:
        return None
    for source in cursor.fetchall():
        row = dict(source) if isinstance(source, sqlite3.Row) else dict(
            zip((column[0] for column in cursor.description or ()), source, strict=True)
        )
        try:
            available = datetime.fromisoformat(
                str(row.get("available_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if available.tzinfo is None or available.utcoffset() is None:
            continue
        if available.astimezone(TPE) <= cutoff:
            return row
    return None


def cleanup_twse_daily_valuation(
    retention_days: int = 200,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    def _run(c: sqlite3.Connection) -> dict[str, Any]:
        ensure_twse_daily_valuation_schema(c)
        dates = [
            str(row["data_date"])
            for row in c.execute(
                "SELECT DISTINCT data_date FROM twse_daily_valuation ORDER BY data_date DESC"
            ).fetchall()
        ]
        if len(dates) <= retention_days:
            return {
                "retention_days": retention_days,
                "total_dates_before": len(dates),
                "deleted_dates": [],
                "total_dates_after": len(dates),
            }
        keep = set(dates[:retention_days])
        deleted = [d for d in dates if d not in keep]
        c.executemany("DELETE FROM twse_daily_valuation WHERE data_date=?", [(d,) for d in deleted])
        return {
            "retention_days": retention_days,
            "total_dates_before": len(dates),
            "deleted_dates": deleted,
            "total_dates_after": len(dates) - len(deleted),
        }

    if conn is not None:
        return _run(conn)
    with closing(db()) as c:
        try:
            c.execute("BEGIN")
            result = _run(c)
            c.commit()
            return result
        except Exception:
            c.rollback()
            raise
