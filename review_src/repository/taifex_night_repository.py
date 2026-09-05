from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from core.taifex_night_schema import ensure_taifex_night_schema


def upsert_taifex_night_rows(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    ensure_taifex_night_schema(conn)
    data = [dict(row) for row in rows]
    if not data:
        return 0
    fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.executemany(
        """
        INSERT INTO taifex_night_daily_snapshot(
            trade_date,contract,contract_month,last,change_pct,volume,
            trading_session,source,source_quality,fetched_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(trade_date,contract) DO UPDATE SET
            contract_month=excluded.contract_month,
            last=excluded.last,
            change_pct=excluded.change_pct,
            volume=excluded.volume,
            trading_session=excluded.trading_session,
            source=excluded.source,
            source_quality=excluded.source_quality,
            fetched_at=excluded.fetched_at
        """,
        [
            (
                row.get("trade_date"), row.get("contract"), row.get("contract_month"),
                row.get("last"), row.get("change_pct"), row.get("volume"),
                row.get("trading_session"), row.get("source"), row.get("source_quality"),
                fetched_at,
            )
            for row in data
        ],
    )
    return len(data)


def prune_taifex_night_rows(conn: sqlite3.Connection, retain_days: int = 400) -> int:
    cutoff = conn.execute(
        """
        SELECT trade_date FROM taifex_night_daily_snapshot
        GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1 OFFSET ?
        """,
        (max(0, retain_days - 1),),
    ).fetchone()
    if not cutoff:
        return 0
    cursor = conn.execute(
        "DELETE FROM taifex_night_daily_snapshot WHERE trade_date < ?",
        (cutoff[0],),
    )
    return max(0, int(cursor.rowcount or 0))


def read_taifex_night_rows_at_cutoff(
    conn: sqlite3.Connection,
    *,
    analysis_cutoff: str,
) -> list[dict[str, Any]]:
    """Read the latest official night-session batch actually fetched by cutoff."""

    try:
        cutoff = datetime.fromisoformat(str(analysis_cutoff).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("analysis_cutoff must be an ISO-8601 timestamp") from exc
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("analysis_cutoff must include an explicit UTC offset")
    cutoff = cutoff.astimezone(timezone.utc)
    try:
        cursor = conn.execute(
            """
            SELECT trade_date,contract,contract_month,last,change_pct,volume,
                   trading_session,source,source_quality,fetched_at
            FROM taifex_night_daily_snapshot
            ORDER BY trade_date DESC,fetched_at DESC,contract
            """
        )
    except sqlite3.OperationalError:
        return []
    columns = [str(column[0]) for column in cursor.description or ()]
    visible: list[dict[str, Any]] = []
    for source in cursor.fetchall():
        row = dict(source) if isinstance(source, sqlite3.Row) else dict(
            zip(columns, source, strict=True)
        )
        try:
            fetched = datetime.fromisoformat(
                str(row.get("fetched_at") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if fetched.tzinfo is None or fetched.utcoffset() is None:
            continue
        if fetched.astimezone(timezone.utc) <= cutoff:
            visible.append(row)
    if not visible:
        return []
    latest_trade_date = max(str(row["trade_date"]) for row in visible)
    selected: dict[str, dict[str, Any]] = {}
    for row in visible:
        if str(row["trade_date"]) != latest_trade_date:
            continue
        contract = str(row["contract"])
        previous = selected.get(contract)
        if previous is None or str(row["fetched_at"]) > str(previous["fetched_at"]):
            selected[contract] = row
    return [selected[contract] for contract in sorted(selected)]
