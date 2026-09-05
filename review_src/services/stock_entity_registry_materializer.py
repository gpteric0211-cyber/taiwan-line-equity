from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from repository.market_microstructure_repository import read_active_stock_master_rows
from repository.single_track_v3_repository import upsert_stock_entity_aliases
from services.stock_entity_registry_service import (
    REVIEWED_ALIASES,
    STOCK_ENTITY_REGISTRY_VERSION,
    stock_names,
)


TPE = ZoneInfo("Asia/Taipei")


def build_stock_entity_registry_rows(
    active_rows: Iterable[Mapping[str, Any]],
    *,
    recorded_at: str | None = None,
) -> list[dict[str, Any]]:
    timestamp = recorded_at or datetime.now(TPE).isoformat(timespec="seconds")
    source_rows = [dict(row) for row in active_rows]
    by_code = {str(row.get("code") or "").zfill(4): row for row in source_rows}
    rows: list[dict[str, Any]] = []
    for source in source_rows:
        code = str(source.get("code") or "").zfill(4)
        names = stock_names(source)
        canonical_name = str(source.get("name") or "")
        trading_name = str(source.get("trading_name") or (names[0] if names else canonical_name))
        for alias in names:
            rows.append(
                {
                    "alias": alias,
                    "stock_code": code,
                    "canonical_name": canonical_name,
                    "trading_name": trading_name,
                    "source": "official_active_stock_master",
                    "effective_from": "1900-01-01",
                    "effective_to": None,
                    "confidence": 1.0,
                    "ambiguity_set": [],
                    "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
                    "reviewed": 1,
                    "updated_at": timestamp,
                }
            )
    for alias in REVIEWED_ALIASES:
        source = by_code.get(str(alias["stock_code"]).zfill(4))
        if source is None:
            continue
        names = stock_names(source)
        rows.append(
            {
                "alias": alias["alias"],
                "stock_code": str(alias["stock_code"]).zfill(4),
                "canonical_name": str(source.get("name") or ""),
                "trading_name": str(source.get("trading_name") or (names[0] if names else source.get("name") or "")),
                "source": alias["source"],
                "effective_from": alias["effective_from"],
                "effective_to": None,
                "confidence": alias["confidence"],
                "ambiguity_set": [],
                "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
                "reviewed": 1,
                "updated_at": timestamp,
            }
        )
    unique = {
        (str(row["alias"]), str(row["stock_code"]), str(row["effective_from"])): row
        for row in rows
    }
    return [unique[key] for key in sorted(unique)]


def materialize_stock_entity_registry(
    conn: sqlite3.Connection | None,
    *,
    active_rows: Iterable[Mapping[str, Any]] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    rows = build_stock_entity_registry_rows(
        list(active_rows) if active_rows is not None else read_active_stock_master_rows(conn)
    )
    if not dry_run and conn is None:
        raise ValueError("conn is required unless dry_run=True")
    written = 0 if dry_run else upsert_stock_entity_aliases(conn, rows)
    return {
        "registry_version": STOCK_ENTITY_REGISTRY_VERSION,
        "active_stock_count": len({row["stock_code"] for row in rows}),
        "alias_count": len(rows),
        "rows_written": written,
        "dry_run": bool(dry_run),
    }
