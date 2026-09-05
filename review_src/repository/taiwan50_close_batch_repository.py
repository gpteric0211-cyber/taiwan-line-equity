from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from repository.full_market_batch_repository import resolve_full_market_analysis_date


def ensure_taiwan50_close_batch_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
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
        """
    )


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def upsert_taiwan50_close_batch(
    conn: sqlite3.Connection,
    *,
    run: dict[str, Any],
    items: list[dict[str, Any]],
    points: list[dict[str, Any]],
) -> None:
    ensure_taiwan50_close_batch_schema(conn)
    data_date = str(run["data_date"])
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT OR REPLACE INTO taiwan50_close_batch_runs(
                data_date, updated_at, timezone, update_mode, is_realtime,
                item_count, error_count, source_status, reason, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data_date,
                run["updated_at"],
                run.get("timezone", "Asia/Taipei"),
                run.get("update_mode", "close_batch"),
                int(bool(run.get("is_realtime", False))),
                int(run.get("item_count") or len(items)),
                int(run.get("error_count") or 0),
                run.get("source_status"),
                run.get("reason"),
                run.get("created_at") or run["updated_at"],
            ),
        )
        conn.execute("DELETE FROM taiwan50_close_batch_items WHERE data_date=?", (data_date,))
        conn.execute("DELETE FROM taiwan50_close_volume_profile_points WHERE data_date=?", (data_date,))
        conn.executemany(
            """
            INSERT OR REPLACE INTO taiwan50_close_batch_items(
                data_date, symbol, name, rank_no, close_price, reference_price,
                support_zone, pressure_zone, poc_price, poc_volume, source_status,
                data_quality, reason, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    data_date,
                    str(item["symbol"]).zfill(4),
                    item.get("name"),
                    item.get("rank_no"),
                    item.get("close_price"),
                    item.get("reference_price"),
                    _json_or_none(item.get("support_zone")),
                    _json_or_none(item.get("pressure_zone")),
                    item.get("poc_price"),
                    item.get("poc_volume"),
                    item.get("source_status"),
                    item.get("data_quality"),
                    item.get("reason"),
                    item.get("updated_at") or run["updated_at"],
                )
                for item in items
            ],
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO taiwan50_close_volume_profile_points(
                data_date, symbol, price, volume, source, updated_at
            ) VALUES(?,?,?,?,?,?)
            """,
            [
                (
                    data_date,
                    str(point["symbol"]).zfill(4),
                    float(point["price"]),
                    int(point["volume"]),
                    point.get("source"),
                    point.get("updated_at") or run["updated_at"],
                )
                for point in points
            ],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def latest_taiwan50_close_batch(
    conn: sqlite3.Connection,
    data_date: str | None = None,
) -> dict[str, Any] | None:
    """Read the newest close batch at the published full-market boundary.

    An explicit historical date is preserved.  The default path must never
    select a locally newer partial day merely because a Taiwan 50 batch row
    already exists for it.
    """

    ensure_taiwan50_close_batch_schema(conn)
    analysis_date = resolve_full_market_analysis_date(conn, data_date)
    if not analysis_date:
        return None
    run = conn.execute(
        """
        SELECT *
        FROM taiwan50_close_batch_runs
        WHERE data_date<=?
        ORDER BY data_date DESC
        LIMIT 1
        """,
        (analysis_date,),
    ).fetchone()
    if not run:
        return None
    data_date = run["data_date"]
    rows = conn.execute(
        """
        SELECT *
        FROM taiwan50_close_batch_items
        WHERE data_date=?
        ORDER BY COALESCE(rank_no, 9999), symbol
        """,
        (data_date,),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        for key in ("support_zone", "pressure_zone"):
            if item.get(key):
                try:
                    item[key] = json.loads(item[key])
                except Exception:
                    item[key] = None
        items.append(item)
    return {"run": dict(run), "items": items}


def cleanup_taiwan50_close_batch(conn: sqlite3.Connection, *, retention_days: int = 200) -> dict[str, Any]:
    ensure_taiwan50_close_batch_schema(conn)
    dates = [
        str(row["data_date"])
        for row in conn.execute(
            "SELECT data_date FROM taiwan50_close_batch_runs ORDER BY data_date DESC"
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
    old_dates = [d for d in dates if d not in keep]
    try:
        conn.execute("BEGIN")
        for d in old_dates:
            conn.execute("DELETE FROM taiwan50_close_batch_runs WHERE data_date=?", (d,))
            conn.execute("DELETE FROM taiwan50_close_batch_items WHERE data_date=?", (d,))
            conn.execute("DELETE FROM taiwan50_close_volume_profile_points WHERE data_date=?", (d,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logging.info(
        "taiwan50 close-batch cleanup retention_days=%s before=%s deleted=%s after=%s",
        retention_days,
        len(dates),
        old_dates,
        len(dates) - len(old_dates),
    )
    return {
        "retention_days": retention_days,
        "total_dates_before": len(dates),
        "deleted_dates": old_dates,
        "total_dates_after": len(dates) - len(old_dates),
    }
