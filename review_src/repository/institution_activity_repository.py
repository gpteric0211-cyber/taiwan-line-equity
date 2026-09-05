from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from core.institution_activity_schema import ensure_institution_activity_schema
from core.provenance_schema import (
    ensure_provenance_schema,
    record_validated_institution_activity_version,
)


def upsert_official_institution_rows(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
) -> int:
    ensure_institution_activity_schema(conn)
    normalized = [dict(row) for row in rows]
    if not normalized:
        return 0
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    ensure_provenance_schema(conn)
    for row in normalized:
        record_validated_institution_activity_version(
            conn,
            row,
            ensure_schema=False,
        )
    activity_sql = """
        INSERT INTO institution_activity_daily(
            trade_date,code,market,
            foreign_buy,foreign_sell,foreign_net,
            trust_buy,trust_sell,trust_net,
            dealer_buy,dealer_sell,dealer_net,
            source,source_quality,fetched_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(trade_date,code) DO UPDATE SET
            market=excluded.market,
            foreign_buy=excluded.foreign_buy,
            foreign_sell=excluded.foreign_sell,
            foreign_net=excluded.foreign_net,
            trust_buy=excluded.trust_buy,
            trust_sell=excluded.trust_sell,
            trust_net=excluded.trust_net,
            dealer_buy=excluded.dealer_buy,
            dealer_sell=excluded.dealer_sell,
            dealer_net=excluded.dealer_net,
            source=excluded.source,
            source_quality=excluded.source_quality,
            fetched_at=excluded.fetched_at
    """
    conn.executemany(
        activity_sql,
        [
            (
                row.get("date"), row.get("code"), row.get("market"),
                row.get("foreign_buy"), row.get("foreign_sell"), row.get("foreign_net"),
                row.get("trust_buy"), row.get("trust_sell"), row.get("trust_net"),
                row.get("dealer_buy"), row.get("dealer_sell"), row.get("dealer_net"),
                row.get("source"), "official", fetched_at,
            )
            for row in normalized
        ],
    )
    legacy_fetched_at = time.time()
    conn.executemany(
        """
        INSERT INTO institution_daily(
            date,code,foreign_net,trust_net,dealer_net,source,updated_at,
            source_quality,fetched_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date,code) DO UPDATE SET
            foreign_net=excluded.foreign_net,
            trust_net=excluded.trust_net,
            dealer_net=excluded.dealer_net,
            source=excluded.source,
            updated_at=excluded.updated_at,
            source_quality=excluded.source_quality,
            fetched_at=excluded.fetched_at
        """,
        [
            (
                row.get("date"), row.get("code"), row.get("foreign_net"),
                row.get("trust_net"), row.get("dealer_net"), row.get("source"),
                legacy_fetched_at, "official", legacy_fetched_at,
            )
            for row in normalized
        ],
    )
    return len(normalized)


def prune_institution_activity(conn: sqlite3.Connection, retain_days: int = 720) -> int:
    cutoff = conn.execute(
        """
        SELECT trade_date FROM institution_activity_daily
        GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1 OFFSET ?
        """,
        (max(0, retain_days - 1),),
    ).fetchone()
    if not cutoff:
        return 0
    cursor = conn.execute(
        "DELETE FROM institution_activity_daily WHERE trade_date < ?",
        (cutoff[0],),
    )
    return max(0, int(cursor.rowcount or 0))
