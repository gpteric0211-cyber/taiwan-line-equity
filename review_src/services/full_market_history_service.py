from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from adapter.taiwan_full_market_history import (
    TPEX_DAILY_QUOTES_URL,
    TWSE_MI_INDEX_URL,
    fetch_tpex_full_market_date,
    fetch_twse_full_market_date,
)
from core.db import db
from core.full_market_batch_schema import ensure_full_market_batch_schema
from core.market_calendar_cache import taiwan_market_day_status
from core.market_foundation_schema import (
    ensure_market_foundation_schema,
    upsert_daily_ohlcv_rows,
)
from core.market_session import recent_market_date_for_eod
from repository.full_market_batch_repository import (
    evaluate_full_market_batch,
    record_full_market_batch,
)
from repository.stock_no_trade_repository import (
    ensure_stock_no_trade_schema,
    upsert_verified_no_trade_dates,
)


MIN_ABSENCE_INFERENCE_COVERAGE = 0.99


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _derived_no_trade_rows(
    conn,
    *,
    market: str,
    trade_date: str,
    missing_codes: set[str],
    official_source: str,
    raw_coverage: float,
) -> list[dict[str, Any]]:
    """Classify corroborated official-report absences as zero-trade dates.

    A missing row is not enough by itself.  It must be corroborated by an
    official trading halt, or by a near-complete official full-market report
    plus an explicit empty/not-found result from the licensed trade feed.
    The result is no-trade metadata, never a fabricated OHLCV bar.
    """

    if not missing_codes:
        return []
    restriction_by_code: dict[str, dict[str, Any]] = {}
    if _table_exists(conn, "official_trading_restriction"):
        placeholders = ",".join("?" for _ in missing_codes)
        cursor = conn.execute(
            f"""
            SELECT code,restriction_type,effective_from,effective_to,
                   reason,source_id,source_quality
            FROM official_trading_restriction
            WHERE code IN ({placeholders})
              AND restriction_type='trading_halt'
              AND LOWER(COALESCE(source_quality,''))='official'
              AND COALESCE(effective_from,announcement_date)<=?
              AND (effective_to IS NULL OR effective_to>=?)
            """,
            (*sorted(missing_codes), trade_date, trade_date),
        )
        for row in cursor.fetchall():
            item = dict(row) if hasattr(row, "keys") else dict(zip((col[0] for col in cursor.description), row))
            restriction_by_code[str(item.get("code") or "")] = item

    empty_feed_by_code: dict[str, dict[str, Any]] = {}
    if raw_coverage >= MIN_ABSENCE_INFERENCE_COVERAGE and _table_exists(conn, "fugle_intraday_capture_runs"):
        placeholders = ",".join("?" for _ in missing_codes)
        cursor = conn.execute(
            f"""
            SELECT code,data_quality,capture_complete,provider_row_count,
                   normalized_row_count,stored_row_count,reason
            FROM fugle_intraday_capture_runs
            WHERE code IN ({placeholders}) AND trade_date=? AND endpoint='trades'
              AND UPPER(COALESCE(data_quality,''))='UNAVAILABLE'
              AND COALESCE(capture_complete,0)=0
              AND COALESCE(provider_row_count,0)=0
              AND COALESCE(normalized_row_count,0)=0
              AND COALESCE(stored_row_count,0)=0
            """,
            (*sorted(missing_codes), trade_date),
        )
        for row in cursor.fetchall():
            item = dict(row) if hasattr(row, "keys") else dict(zip((col[0] for col in cursor.description), row))
            reason = str(item.get("reason") or "").upper()
            if "EMPTY_TRADES" in reason or "HTTP_STATUS=404" in reason or "RESOURCE NOT FOUND" in reason:
                empty_feed_by_code[str(item.get("code") or "")] = item

    source_url = TWSE_MI_INDEX_URL if market == "listed" else TPEX_DAILY_QUOTES_URL
    rows: list[dict[str, Any]] = []
    for code in sorted(missing_codes):
        restriction = restriction_by_code.get(code)
        empty_feed = empty_feed_by_code.get(code)
        if not restriction and not empty_feed:
            continue
        corroboration = "official_trading_halt" if restriction else "licensed_trade_feed_empty"
        rows.append({
            "trade_date": trade_date,
            "code": code,
            "market": market,
            "reason": f"official_daily_report_absent_{corroboration}",
            "source": official_source,
            "source_url": source_url,
            "source_quality": "official",
            "evidence": {
                "official_report_source": official_source,
                "official_report_raw_coverage_pct": round(raw_coverage * 100, 4),
                "corroboration": corroboration,
                "restriction": restriction,
                "licensed_feed": empty_feed,
                "reported_volume": 0,
            },
        })
    return rows


def recent_verified_trading_dates(
    *,
    days: int,
    end_date: str | None = None,
) -> list[str]:
    end = date.fromisoformat(end_date or recent_market_date_for_eod())
    target = max(int(days), 1)
    values: list[str] = []
    current = end
    while len(values) < target:
        status = taiwan_market_day_status(current)
        if not status.get("verified"):
            raise RuntimeError(f"official Taiwan calendar unavailable for {current.isoformat()}")
        if status.get("is_trading_day"):
            values.append(current.isoformat())
        current -= timedelta(days=1)
    return values


def _expected_codes(conn, market: str, trade_date: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT code
        FROM stock_master
        WHERE market=? AND security_type='stock'
          AND (first_seen_date IS NULL OR first_seen_date<=?)
          AND (is_active=1 OR (last_seen_date IS NOT NULL AND last_seen_date>=?))
        """,
        (market, trade_date, trade_date),
    ).fetchall()
    return {str(row[0]) for row in rows}


def refresh_full_market_history_date(
    trade_date: str,
    *,
    dry_run: bool = False,
    twse_fetcher: Callable[[str], dict[str, Any]] = fetch_twse_full_market_date,
    tpex_fetcher: Callable[[str], dict[str, Any]] = fetch_tpex_full_market_date,
) -> dict[str, Any]:
    """Fetch one exact official date for both markets and persist idempotently."""

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=2) as pool:
        twse_future = pool.submit(twse_fetcher, trade_date)
        tpex_future = pool.submit(tpex_fetcher, trade_date)
        fetched = [twse_future.result(), tpex_future.result()]
    fetched_at = time.time()
    source_results: list[dict[str, Any]] = []
    prepared_results: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
    total_written = 0
    no_trade_written = 0
    with closing(db()) as conn:
        for result in fetched:
            market = str(result.get("market") or "")
            expected = _expected_codes(conn, market, trade_date)
            rows = [
                dict(row)
                for row in result.get("rows") or []
                if str(row.get("code") or "") in expected
            ]
            no_trade = [
                dict(row)
                for row in result.get("verified_no_trade_dates") or []
                if str(row.get("code") or "") in expected
            ]
            observed = {str(row.get("code") or "") for row in rows + no_trade}
            raw_coverage = len(observed) / len(expected) if expected else 0.0
            derived_no_trade = _derived_no_trade_rows(
                conn,
                market=market,
                trade_date=trade_date,
                missing_codes=expected - observed,
                official_source=str(result.get("source") or ""),
                raw_coverage=raw_coverage,
            )
            no_trade.extend(derived_no_trade)
            observed.update(str(row.get("code") or "") for row in derived_no_trade)
            coverage = len(observed) / len(expected) if expected else 0.0
            storage_qualified = bool(
                result.get("ok")
                and result.get("data_date") == trade_date
                and coverage >= 0.95
            )
            prepared_results.append((rows, no_trade))
            source_results.append(
                {
                    "market": market,
                    "source": result.get("source"),
                    "ok": storage_qualified,
                    "storage_qualified": storage_qualified,
                    "fetch_ok": bool(result.get("ok")),
                    "data_date": result.get("data_date"),
                    "expected_codes": len(expected),
                    "observed_codes": len(observed),
                    "coverage_pct": round(coverage * 100, 2),
                    "ohlcv_rows": len(rows),
                    "no_trade_rows": len(no_trade),
                    "derived_no_trade_rows": len(derived_no_trade),
                    "derived_no_trade_codes": [str(row.get("code") or "") for row in derived_no_trade[:20]],
                    "missing_code_sample": sorted(expected - observed)[:20],
                    "error": result.get("error"),
                }
            )
        storage_allowed = bool(source_results and all(item["storage_qualified"] for item in source_results))
        batch: dict[str, Any] | None = None
        if not dry_run:
            # Schema setup is deliberately outside the publication transaction:
            # row writes, the audited run, and the complete marker then commit
            # or roll back as one unit.
            ensure_market_foundation_schema(conn)
            ensure_stock_no_trade_schema(conn)
            ensure_full_market_batch_schema(conn)
            conn.commit()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if storage_allowed:
                    for rows, no_trade in prepared_results:
                        for row in rows:
                            row["updated_at"] = fetched_at
                            row["fetched_at"] = fetched_at
                        no_trade_written += upsert_verified_no_trade_dates(
                            conn,
                            no_trade,
                            ensure_schema=False,
                        )
                        total_written += upsert_daily_ohlcv_rows(
                            conn,
                            rows,
                            ensure_schema=False,
                        )
                evaluation = evaluate_full_market_batch(conn, trade_date)
                batch = record_full_market_batch(
                    conn,
                    evaluation,
                    storage_status="stored" if storage_allowed else "not_stored",
                    source_summary=source_results,
                    started_at=started_at,
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        ok = bool(
            storage_allowed
            if dry_run
            else storage_allowed and batch and batch.get("batch_status") == "complete"
        )
    return {
        "ok": ok,
        "status": "ok" if ok and not dry_run else ("dry_run" if dry_run else "partial"),
        "batch_status": None if dry_run else (batch or {}).get("batch_status"),
        "storage_allowed": storage_allowed,
        "trade_date": trade_date,
        "dry_run": dry_run,
        "rows_written": total_written,
        "no_trade_rows_written": no_trade_written,
        "source_results": source_results,
        "batch": batch,
    }
