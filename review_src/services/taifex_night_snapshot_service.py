from __future__ import annotations
from contextlib import closing

from typing import Any

from adapter.taifex_night import fetch_taifex_night_snapshot
from core.db import db
from repository.taifex_night_repository import prune_taifex_night_rows, upsert_taifex_night_rows


def refresh_taifex_night_snapshot(*, dry_run: bool = False) -> dict[str, Any]:
    fetched = fetch_taifex_night_snapshot()
    if not fetched.get("ok"):
        return {**fetched, "writes_db": False, "rows_written": 0}
    rows = list(fetched.get("items") or [])
    written = 0
    pruned = 0
    if not dry_run:
        with closing(db()) as conn, conn:
            written = upsert_taifex_night_rows(conn, rows)
            pruned = prune_taifex_night_rows(conn)
            conn.execute("PRAGMA optimize")
            conn.commit()
    return {
        "ok": True,
        "status": "dry_run" if dry_run else "ok",
        "writes_db": not dry_run and written > 0,
        "trade_date": fetched.get("trade_date"),
        "rows_fetched": len(rows),
        "rows_written": written,
        "rows_pruned": pruned,
    }
