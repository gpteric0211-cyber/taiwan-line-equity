from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from core.trading_restriction_schema import ensure_trading_restriction_schema


REQUIRED_SOURCES = {
    "listed": ("twse_attention", "twse_disposition", "twse_altered", "twse_halt"),
    "otc": ("tpex_attention", "tpex_disposition", "tpex_cmode"),
}


def upsert_trading_restrictions(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> int:
    ensure_trading_restriction_schema(conn)
    data = [dict(row) for row in rows]
    if not data:
        return 0
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT INTO official_trading_restriction(
            event_key,code,company_name,market,restriction_type,
            announcement_date,effective_from,effective_to,reason,
            source_id,source_quality,fetched_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_key) DO UPDATE SET
            company_name=excluded.company_name,
            reason=excluded.reason,
            fetched_at=excluded.fetched_at
        """,
        [
            (
                row.get("event_key"), row.get("code"), row.get("company_name"),
                row.get("market"), row.get("restriction_type"),
                row.get("announcement_date"), row.get("effective_from"),
                row.get("effective_to"), row.get("reason"), row.get("source_id"),
                row.get("source_quality") or "official", fetched_at,
            )
            for row in data
        ],
    )
    return len(data)


def record_trading_restriction_source_runs(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> int:
    ensure_trading_restriction_schema(conn)
    data = [dict(row) for row in rows]
    if not data:
        return 0
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT INTO official_trading_restriction_source_run(
            data_date,market,source_id,status,rows_received,fetched_at,error_summary
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(data_date,source_id) DO UPDATE SET
            status=excluded.status,
            rows_received=excluded.rows_received,
            fetched_at=excluded.fetched_at,
            error_summary=excluded.error_summary
        """,
        [
            (
                row.get("data_date"), row.get("market"), row.get("source_id"),
                "ok" if row.get("ok") else "failed",
                int(row.get("rows_received") or 0), fetched_at,
                str(row.get("error") or "")[:1000] or None,
            )
            for row in data
        ],
    )
    return len(data)


def read_trading_restriction_context(
    conn: sqlite3.Connection,
    *,
    code: str,
    market: str,
    reference_date: str,
) -> dict[str, Any]:
    normalized_market = "otc" if str(market).lower() in {"otc", "上櫃"} else "listed"
    required = REQUIRED_SOURCES[normalized_market]
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    needed_tables = {
        "official_trading_restriction",
        "official_trading_restriction_source_run",
    }
    if not needed_tables.issubset(tables):
        return {
            "ready": False,
            "status": "unavailable",
            "reason": "official restriction snapshot has not been initialized",
            "market": normalized_market,
            "reference_date": reference_date,
            "required_sources": list(required),
            "source_states": {},
            "items": [],
        }
    placeholders = ",".join("?" for _ in required)
    source_rows = conn.execute(
        f"""
        SELECT source_id,status,rows_received,fetched_at
        FROM official_trading_restriction_source_run
        WHERE data_date=? AND source_id IN ({placeholders})
        """,
        (reference_date, *required),
    ).fetchall()
    source_states = {str(row["source_id"]): str(row["status"]) for row in source_rows}
    ready = all(source_states.get(source_id) == "ok" for source_id in required)
    event_rows = conn.execute(
        """
        SELECT restriction_type,announcement_date,effective_from,effective_to,
               company_name,reason,source_id,source_quality,fetched_at
        FROM official_trading_restriction
        WHERE code=? AND market=? AND announcement_date<=?
          AND (effective_to IS NULL OR effective_to>=?)
          AND (
                restriction_type='disposition'
                OR COALESCE(effective_from,announcement_date)<=?
          )
        ORDER BY CASE restriction_type
                    WHEN 'trading_halt' THEN 0
                    WHEN 'disposition' THEN 1
                    WHEN 'altered_trading' THEN 2
                    WHEN 'managed_stock' THEN 3
                    WHEN 'periodic_trading' THEN 4
                    ELSE 5
                 END,
                 announcement_date DESC
        """,
        (code, normalized_market, reference_date, reference_date, reference_date),
    ).fetchall()
    return {
        "ready": ready,
        "status": "ok" if ready else "source_delayed",
        "reason": "ok" if ready else "one or more official restriction snapshots are missing",
        "market": normalized_market,
        "reference_date": reference_date,
        "required_sources": list(required),
        "source_states": source_states,
        "items": [dict(row) for row in event_rows],
    }


def prune_trading_restrictions(conn: sqlite3.Connection, retain_days: int = 730) -> int:
    cutoff = conn.execute(
        """
        SELECT data_date FROM official_trading_restriction_source_run
        GROUP BY data_date ORDER BY data_date DESC LIMIT 1 OFFSET ?
        """,
        (max(0, int(retain_days) - 1),),
    ).fetchone()
    if not cutoff:
        return 0
    cutoff_date = str(cutoff[0])
    first = conn.execute(
        "DELETE FROM official_trading_restriction WHERE announcement_date < ?",
        (cutoff_date,),
    )
    second = conn.execute(
        "DELETE FROM official_trading_restriction_source_run WHERE data_date < ?",
        (cutoff_date,),
    )
    return max(0, int(first.rowcount or 0)) + max(0, int(second.rowcount or 0))
