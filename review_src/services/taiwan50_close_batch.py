from __future__ import annotations

import logging
import time
from contextlib import closing
from typing import Any

from core.components import read_components
from core.db import db
from core.utils import now_tpe, normalize_date, parse_num
from price_volume import analyze_volume_structure
from repository.taiwan50_close_batch_repository import (
    cleanup_taiwan50_close_batch,
    ensure_taiwan50_close_batch_schema,
    upsert_taiwan50_close_batch,
)


INSUFFICIENT_REASON = "價量分布資料不足，未使用最高最低價假裝支撐賣壓"


def _taipei_timestamp() -> str:
    return now_tpe().strftime("%Y-%m-%d %H:%M:%S")


def _component_codes() -> list[dict[str, str]]:
    comps = read_components()
    clean = []
    seen = set()
    for idx, item in enumerate(comps, start=1):
        code = str(item.get("code", "")).strip().zfill(4)
        if not code or code in seen:
            continue
        seen.add(code)
        clean.append({"code": code, "name": item.get("name", ""), "rank_no": idx})
    if len(clean) < 50:
        raise RuntimeError(f"taiwan50 component list is incomplete: {len(clean)}/50")
    return clean


def _latest_common_trade_date(conn, codes: list[str]) -> str | None:
    rows = conn.execute(
        f"""
        SELECT date, COUNT(DISTINCT code) AS c
        FROM history_price
        WHERE code IN ({",".join(["?"] * len(codes))})
          AND close IS NOT NULL
        GROUP BY date
        HAVING c >= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (*codes, len(codes)),
    ).fetchone()
    if rows:
        return str(rows["date"])
    rows = conn.execute(
        f"""
        SELECT date, COUNT(DISTINCT code) AS c
        FROM eod_price
        WHERE code IN ({",".join(["?"] * len(codes))})
          AND close IS NOT NULL
        GROUP BY date
        HAVING c >= ?
        ORDER BY date DESC
        LIMIT 1
        """,
        (*codes, len(codes)),
    ).fetchone()
    return str(rows["date"]) if rows else None


def _close_price_for(conn, code: str, data_date: str) -> float | None:
    row = conn.execute(
        "SELECT close FROM history_price WHERE code=? AND date=? LIMIT 1",
        (code, data_date),
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT close FROM eod_price WHERE code=? AND date=? LIMIT 1",
            (code, data_date),
        ).fetchone()
    return parse_num(row["close"]) if row else None


def _profile_points_for(conn, code: str, data_date: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT price, volume_lots
        FROM price_volume_distribution
        WHERE stock_id=? AND trade_date=?
        ORDER BY price
        """,
        (code, data_date),
    ).fetchall()
    return [
        {"price": float(row["price"]), "volume": int(row["volume_lots"] or 0)}
        for row in rows
        if parse_num(row["price"]) is not None and parse_num(row["volume_lots"]) is not None
    ]


def update_taiwan50_close_batch(
    data_date: str | None = None,
    *,
    retention_days: int = 200,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build close-after Taiwan50 price-volume batch from existing SQLite data."""
    started = time.perf_counter()
    updated_at = _taipei_timestamp()
    comps = _component_codes()
    codes = [c["code"] for c in comps]
    with closing(db()) as conn:
        ensure_taiwan50_close_batch_schema(conn)
        effective_date = normalize_date(data_date) if data_date else _latest_common_trade_date(conn, codes)
        if not effective_date:
            raise RuntimeError("cannot determine a complete Taiwan50 close date from local DB")
        items: list[dict[str, Any]] = []
        points: list[dict[str, Any]] = []
        error_count = 0
        for comp in comps:
            code = comp["code"]
            close_price = _close_price_for(conn, code, effective_date)
            profile = _profile_points_for(conn, code, effective_date)
            analysis = analyze_volume_structure(profile, close_price)
            if analysis.get("source_status") != "ok":
                error_count += 1
            for point in profile:
                points.append({
                    "data_date": effective_date,
                    "symbol": code,
                    "price": point["price"],
                    "volume": point["volume"],
                    "source": "price_volume_distribution",
                    "updated_at": updated_at,
                })
            items.append({
                "data_date": effective_date,
                "symbol": code,
                "name": comp.get("name", ""),
                "rank_no": comp.get("rank_no"),
                "close_price": close_price,
                "reference_price": close_price,
                "support_zone": analysis.get("support_zone"),
                "pressure_zone": analysis.get("pressure_zone"),
                "poc_price": analysis.get("poc_price"),
                "poc_volume": analysis.get("poc_volume"),
                "source_status": analysis.get("source_status") or "insufficient_volume_profile",
                "data_quality": analysis.get("data_quality") or "missing",
                "reason": analysis.get("reason") or INSUFFICIENT_REASON,
                "updated_at": updated_at,
            })
        run = {
            "data_date": effective_date,
            "updated_at": updated_at,
            "timezone": "Asia/Taipei",
            "update_mode": "close_batch",
            "is_realtime": False,
            "item_count": len(items),
            "error_count": error_count,
            "source_status": "partial" if error_count else "ok",
            "reason": f"{error_count} stocks without true price-volume rows" if error_count else "ok",
            "created_at": updated_at,
        }
        if not dry_run:
            upsert_taiwan50_close_batch(conn, run=run, items=items, points=points)
            cleanup = cleanup_taiwan50_close_batch(conn, retention_days=retention_days)
        else:
            cleanup = {"dry_run": True}
    duration = round(time.perf_counter() - started, 3)
    result = {
        "ok": True,
        "data_date": run["data_date"],
        "total_count": len(items),
        "success_count": len(items) - error_count,
        "error_count": error_count,
        "duration_seconds": duration,
        "updated_at": updated_at,
        "timezone": "Asia/Taipei",
        "dry_run": dry_run,
        "cleanup": cleanup,
    }
    logging.info(
        "taiwan50 close-batch data_date=%s total=%s success=%s errors=%s duration=%s",
        result["data_date"],
        result["total_count"],
        result["success_count"],
        result["error_count"],
        result["duration_seconds"],
    )
    return result
