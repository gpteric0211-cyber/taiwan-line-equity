from __future__ import annotations

import sqlite3
from core.material_news_schema import archive_material_news
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from core.official_event_schema import ensure_official_event_schema
from core.market_timing import availability_contract


def upsert_official_events(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    ensure_official_event_schema(conn)
    data = [dict(row) for row in rows]
    if not data:
        return 0
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    timing = availability_contract(fetched_at=fetched_at)
    conn.executemany(
        """
        INSERT INTO official_company_event(
            event_key,disclosed_date,disclosed_time,fact_date,code,company_name,
            subject,explanation,article_code,market,attention_level,source,
            source_quality,fetched_at,available_at,market_session,
            effective_tw_trade_date
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_key) DO UPDATE SET
            disclosed_time=excluded.disclosed_time,
            explanation=excluded.explanation,
            article_code=excluded.article_code,
            attention_level=excluded.attention_level,
            fetched_at=excluded.fetched_at,
            available_at=COALESCE(official_company_event.available_at,excluded.available_at),
            market_session=CASE
                WHEN official_company_event.market_session='unknown'
                THEN excluded.market_session
                ELSE official_company_event.market_session
            END,
            effective_tw_trade_date=COALESCE(
                official_company_event.effective_tw_trade_date,
                excluded.effective_tw_trade_date
            )
        """,
        [
            (
                row.get("event_key"), row.get("disclosed_date"), row.get("disclosed_time"),
                row.get("fact_date"), row.get("code"), row.get("company_name"),
                row.get("subject"), row.get("explanation"), row.get("article_code"),
                row.get("market"), row.get("attention_level"), row.get("source"),
                row.get("source_quality"), fetched_at, timing["available_at"],
                timing["market_session"], timing["effective_tw_trade_date"],
            )
            for row in data
        ],
    )
    archive_material_news(conn, source_tables=("official_company_event",))
    return len(data)


def prune_official_events(conn: sqlite3.Connection, retain_days: int = 730) -> int:
    archive_material_news(conn, source_tables=("official_company_event",))
    cutoff = conn.execute(
        """
        SELECT disclosed_date FROM official_company_event
        GROUP BY disclosed_date ORDER BY disclosed_date DESC LIMIT 1 OFFSET ?
        """,
        (max(0, retain_days - 1),),
    ).fetchone()
    if not cutoff:
        return 0
    cursor = conn.execute(
        "DELETE FROM official_company_event WHERE disclosed_date < ?",
        (cutoff[0],),
    )
    return max(0, int(cursor.rowcount or 0))
