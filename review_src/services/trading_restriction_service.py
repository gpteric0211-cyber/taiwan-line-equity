from __future__ import annotations

from contextlib import closing
from typing import Any

from adapter.official_trading_restrictions import fetch_official_trading_restrictions
from core.db import db
from core.material_news_schema import archive_material_news
from repository.trading_restriction_repository import (
    prune_trading_restrictions,
    record_trading_restriction_source_runs,
    upsert_trading_restrictions,
)


def refresh_official_trading_restrictions(
    *,
    dry_run: bool = False,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    fetched = fetch_official_trading_restrictions(as_of_date=as_of_date)
    items = list(fetched.get("items") or [])
    sources = list(fetched.get("sources") or [])
    written = 0
    source_runs_written = 0
    pruned = 0
    if not dry_run:
        with closing(db()) as conn:
            written = upsert_trading_restrictions(conn, items)
            source_runs_written = record_trading_restriction_source_runs(conn, sources)
            archive_material_news(conn, {"official_trading_restriction"})
            pruned = prune_trading_restrictions(conn)
            conn.execute("PRAGMA optimize")
            conn.commit()
    return {
        "ok": bool(fetched.get("ok")),
        "status": "dry_run" if dry_run and fetched.get("ok") else fetched.get("status"),
        "dry_run": dry_run,
        "writes_db": bool(not dry_run and (written or source_runs_written)),
        "data_date": fetched.get("data_date"),
        "rows_fetched": len(items),
        "rows_written": written,
        "source_runs_written": source_runs_written,
        "rows_pruned": pruned,
        "sources": [
            {
                key: row.get(key)
                for key in (
                    "market",
                    "source_id",
                    "ok",
                    "status",
                    "rows_received",
                    "raw_rows_received",
                    "candidate_rows_received",
                    "data_date",
                    "error",
                )
                if row.get(key) is not None
            }
            for row in sources
        ],
    }
