from __future__ import annotations
from contextlib import closing

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from adapter.tpex_institution import fetch_tpex_institution_dry_run
from adapter.twse_institution import fetch_twse_institution_dry_run
from core.db import db
from repository.institution_activity_repository import (
    prune_institution_activity,
    upsert_official_institution_rows,
)


def _net_checks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    invalid: list[dict[str, Any]] = []
    for row in rows:
        for prefix in ("foreign", "trust", "dealer"):
            buy = row.get(f"{prefix}_buy")
            sell = row.get(f"{prefix}_sell")
            net = row.get(f"{prefix}_net")
            if None in (buy, sell, net) or int(buy) - int(sell) != int(net):
                invalid.append({"code": row.get("code"), "field": prefix})
                break
    return invalid


def refresh_official_institution_snapshot(
    *,
    trade_date: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        twse_future = pool.submit(
            fetch_twse_institution_dry_run, trade_date, include_items=True
        )
        tpex_future = pool.submit(
            fetch_tpex_institution_dry_run, trade_date, include_items=True
        )
        twse = twse_future.result()
        tpex = tpex_future.result()
    sources = [twse, tpex]
    source_dates = {str(item.get("data_date") or "") for item in sources if item.get("ok")}
    if not all(item.get("ok") for item in sources) or len(source_dates) != 1:
        return {
            "ok": False,
            "status": "source_delayed",
            "writes_db": False,
            "source_dates": sorted(source_dates),
            "sources": [
                {k: v for k, v in item.items() if k not in {"items", "raw_response_snippet"}}
                for item in sources
            ],
        }
    selected_date = next(iter(source_dates))
    if trade_date and selected_date != trade_date:
        return {
            "ok": False,
            "status": "source_delayed",
            "writes_db": False,
            "requested_date": trade_date,
            "source_date": selected_date,
        }
    rows = [dict(row) for item in sources for row in item.get("items") or []]
    invalid = _net_checks(rows)
    if invalid:
        return {
            "ok": False,
            "status": "invalid",
            "writes_db": False,
            "source_date": selected_date,
            "row_count": len(rows),
            "invalid_net_rows": len(invalid),
            "invalid_examples": invalid[:10],
        }
    written = 0
    pruned = 0
    if not dry_run:
        with closing(db()) as conn, conn:
            written = upsert_official_institution_rows(conn, rows)
            pruned = prune_institution_activity(conn)
            conn.execute("PRAGMA optimize")
            conn.commit()
    return {
        "ok": True,
        "status": "dry_run" if dry_run else "ok",
        "writes_db": not dry_run and written > 0,
        "source_date": selected_date,
        "row_count": len(rows),
        "listed_rows": len(twse.get("items") or []),
        "otc_rows": len(tpex.get("items") or []),
        "rows_written": written,
        "rows_pruned": pruned,
        "formula_input": "official gross buy/sell/net shares; no broker-identity inference",
    }
