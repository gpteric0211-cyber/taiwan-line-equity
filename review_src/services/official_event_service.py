from __future__ import annotations
from contextlib import closing

from typing import Any

from adapter.official_company_events import fetch_official_company_events
from core.db import db
from repository.official_event_repository import prune_official_events, upsert_official_events


def refresh_official_company_events(
    *,
    dry_run: bool = False,
    expected_date: str | None = None,
) -> dict[str, Any]:
    """Refresh the official daily disclosure feeds without hiding stale data.

    The two endpoints are daily snapshots.  A successful HTTP response can
    still contain the preceding trading day's rows, so transport success alone
    is not sufficient when the caller knows the expected publication date.
    Older rows remain stored for provenance, but the refresh is explicitly
    reported as ``source_delayed``.
    """

    fetched = fetch_official_company_events()
    if not fetched.get("ok"):
        return {
            **fetched,
            "expected_date": expected_date,
            "writes_db": False,
            "rows_written": 0,
        }
    rows = list(fetched.get("items") or [])
    source_markets = {
        str(source.get("market") or "").strip()
        for source in (fetched.get("sources") or [])
        if source.get("ok")
    }
    latest_dates = {
        market: max(
            (
                str(row.get("disclosed_date") or "")
                for row in rows
                if str(row.get("market") or "") == market
            ),
            default="",
        )
        for market in sorted(source_markets)
    }
    delayed_markets = [
        market
        for market, latest_date in latest_dates.items()
        if expected_date and latest_date < expected_date
    ]
    written = 0
    pruned = 0
    if not dry_run:
        with closing(db()) as conn, conn:
            written = upsert_official_events(conn, rows)
            pruned = prune_official_events(conn)
            conn.execute("PRAGMA optimize")
            conn.commit()
    fresh = not delayed_markets
    return {
        "ok": fresh,
        "status": (
            "source_delayed"
            if delayed_markets
            else "dry_run"
            if dry_run
            else "ok"
        ),
        "expected_date": expected_date,
        "latest_dates": latest_dates,
        "source_delayed_markets": delayed_markets,
        "writes_db": not dry_run and written > 0,
        "rows_fetched": len(rows),
        "rows_written": written,
        "rows_pruned": pruned,
        "sources": fetched.get("sources"),
    }
