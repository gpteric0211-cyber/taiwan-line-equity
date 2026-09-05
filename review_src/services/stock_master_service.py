from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from adapter.tpex import fetch_tpex_stock_list
from adapter.twse_company import fetch_twse_company_list
from core.db import db
from core.market_analytics_schema import ensure_market_analytics_schema
from repository.company_size_repository import upsert_company_size_snapshots
from repository.market_analytics_repository import upsert_stock_master_rows
from repository.stock_universe_repository import record_stock_universe_revision


TPE = ZoneInfo("Asia/Taipei")


def _normalize_tpex_master_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(row.get("code") or "").zfill(4),
        "name": str(row.get("name") or "").strip(),
        "market": "otc",
        "exchange": "TPEX",
        "security_type": "stock",
        "listing_date": row.get("listing_date"),
        "data_date": row.get("data_date"),
        "paid_in_capital_twd": row.get("paid_in_capital_twd"),
        "issued_shares": row.get("issued_shares"),
        "source": "TPEX_COMPANY_OPENAPI",
        "source_status": "ok",
    }


def sync_official_stock_master(
    *,
    dry_run: bool = False,
    twse_fetcher: Callable[[], dict[str, Any]] = fetch_twse_company_list,
    tpex_fetcher: Callable[[], dict[str, Any]] = fetch_tpex_stock_list,
) -> dict[str, Any]:
    """Synchronize active listed/OTC company codes from official company APIs."""

    fetched = [twse_fetcher(), tpex_fetcher()]
    normalized_sources: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    for market, result in zip(("listed", "otc"), fetched):
        source_rows = list(result.get("items") or []) if result.get("ok") else []
        rows = (
            [dict(row) for row in source_rows]
            if market == "listed"
            else [_normalize_tpex_master_row(row) for row in source_rows]
        )
        normalized_sources.append((market, result, rows))

    observed_at = datetime.now(TPE)
    now_text = observed_at.strftime("%Y-%m-%d %H:%M:%S")
    source_summary: list[dict[str, Any]] = []
    written = 0
    size_snapshots_written = 0
    deactivated = 0
    universe_versions_written = 0
    with closing(db()) as conn:
        if not dry_run:
            ensure_market_analytics_schema(conn)
        history_ranges = {
            str(row["code"]): (row["first_date"], row["last_date"])
            for row in conn.execute(
                """
                SELECT code,MIN(date) AS first_date,MAX(date) AS last_date
                FROM history_price
                GROUP BY code
                """
            ).fetchall()
        }
        for market, result, rows in normalized_sources:
            source_summary.append(
                {
                    "market": market,
                    "ok": bool(result.get("ok")),
                    "source": result.get("source"),
                    "rows": int(result.get("rows") or 0),
                    "valid_rows": len(rows),
                    "error": result.get("error"),
                }
            )
            if not rows:
                continue
            values: list[dict[str, Any]] = []
            active_codes: list[str] = []
            for row in rows:
                code = str(row.get("code") or "").zfill(4)
                if len(code) != 4 or not code.isdigit():
                    continue
                active_codes.append(code)
                first_history, last_history = history_ranges.get(code, (None, None))
                values.append(
                    {
                        "code": code,
                        "name": row.get("name"),
                        "market": market,
                        "exchange": "TWSE" if market == "listed" else "TPEX",
                        "security_type": "stock",
                        "is_active": 1,
                        "source": row.get("source") or result.get("source") or "OFFICIAL_COMPANY_OPENAPI",
                        "source_status": "ok",
                        "first_seen_date": row.get("listing_date") or first_history,
                        "last_seen_date": last_history,
                        "updated_at": now_text,
                    }
                )
            if dry_run:
                continue
            placeholders = ",".join("?" for _ in active_codes)
            if placeholders:
                before = conn.total_changes
                conn.execute(
                    f"""
                    UPDATE stock_master
                    SET is_active=0,source_status='not_in_latest_official_list',updated_at=?
                    WHERE market=? AND code NOT IN ({placeholders})
                    """,
                    (now_text, market, *active_codes),
                )
                deactivated += conn.total_changes - before
            written += upsert_stock_master_rows(conn, values)
            source_dates = sorted(
                {
                    str(row.get("data_date"))
                    for row in rows
                    if row.get("data_date")
                }
            )
            universe_revision = record_stock_universe_revision(
                conn,
                market=market,
                members=values,
                source_id=str(result.get("source") or values[0]["source"]),
                source_url=result.get("url"),
                observed_at=observed_at,
                membership_effective_from=(source_dates[0] if len(source_dates) == 1 else None),
            )
            universe_versions_written += int(bool(universe_revision["written"]))
            size_rows = [
                {
                    "data_date": row.get("data_date"),
                    "code": str(row.get("code") or "").zfill(4),
                    "market": market,
                    "paid_in_capital_twd": row.get("paid_in_capital_twd"),
                    "issued_shares": row.get("issued_shares"),
                    "source_id": row.get("source") or result.get("source"),
                }
                for row in rows
            ]
            size_snapshots_written += upsert_company_size_snapshots(
                conn,
                size_rows,
                observed_at=observed_at.isoformat(timespec="seconds"),
            )
        if not dry_run:
            conn.commit()

    expected_markets = {item[0] for item in normalized_sources}
    successful_markets = {item[0] for item in normalized_sources if item[2]}
    status = "ok" if successful_markets == expected_markets else "partial"
    return {
        "ok": status == "ok",
        "status": status,
        "dry_run": dry_run,
        "source_results": source_summary,
        "active_rows_seen": sum(len(item[2]) for item in normalized_sources),
        "rows_written": written,
        "company_size_snapshots_written": size_snapshots_written,
        "universe_versions_written": universe_versions_written,
        "rows_deactivated": deactivated,
    }
