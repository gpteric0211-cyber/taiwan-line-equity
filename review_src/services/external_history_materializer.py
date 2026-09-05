from __future__ import annotations

"""Explicit background materialization for external market context history.

Network retrieval is completed before each database write.  Callers are
expected to run this service against an isolated candidate database; request
paths must never invoke it.
"""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import date, timedelta
from typing import Any

from adapter.global_market_history import fetch_global_market_history_rows
from adapter.official_valuation_history import (
    fetch_tpex_daily_valuation,
    fetch_twse_daily_valuation,
)
from adapter.taifex_night_history import fetch_taifex_futures_range
from core.db import db
from repository.global_market_repository import (
    prune_global_market_rows,
    upsert_global_market_rows,
)
from repository.taifex_night_repository import (
    prune_taifex_night_rows,
    upsert_taifex_night_rows,
)
from repository.twse_valuation_repository import (
    cleanup_twse_daily_valuation,
    upsert_twse_daily_valuations,
)
from services.global_market_snapshot_service import GLOBAL_MARKET_TICKERS


def verified_price_dates(*, limit: int) -> list[str]:
    """Return recent local official-price dates, oldest first."""

    with closing(db()) as conn:
        rows = conn.execute(
            """
            SELECT date
            FROM history_price
            WHERE date IS NOT NULL AND date<>'' AND close IS NOT NULL
            GROUP BY date
            HAVING COUNT(*)>=500
            ORDER BY date DESC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        ).fetchall()
    return sorted(str(row[0]) for row in rows)


def _month_ranges(start_date: date, end_date: date) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        month_end = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        chunk_end = min(month_end, end_date, cursor + timedelta(days=31))
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return ranges


def materialize_global_history(
    *,
    calendar_days: int = 760,
    retain_market_days: int = 400,
    max_workers: int = 4,
    fetcher: Callable[..., dict[str, Any]] = fetch_global_market_history_rows,
) -> dict[str, Any]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 4))) as executor:
        futures = {
            executor.submit(fetcher, ticker, calendar_days=calendar_days): ticker
            for ticker in GLOBAL_MARKET_TICKERS
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results[ticker] = future.result()
            except Exception as exc:
                results[ticker] = {"ok": False, "rows": [], "error": str(exc)}

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    incomplete_session_rows_skipped = 0
    for ticker, display_name in GLOBAL_MARKET_TICKERS.items():
        result = results.get(ticker) or {}
        incomplete_session_rows_skipped += int(
            result.get("incomplete_session_rows_skipped") or 0
        )
        if not result.get("ok"):
            failures.append({"ticker": ticker, "reason": str(result.get("error") or "empty")})
            continue
        for source_row in result.get("rows") or []:
            row = dict(source_row)
            row["display_name"] = display_name
            rows.append(row)

    with closing(db()) as conn:
        conn.execute("BEGIN")
        written = upsert_global_market_rows(conn, rows)
        pruned = prune_global_market_rows(conn, retain_market_days=retain_market_days)
        conn.commit()
        coverage = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                """
                SELECT ticker,COUNT(DISTINCT market_date)
                FROM global_market_daily_snapshot
                GROUP BY ticker
                """
            ).fetchall()
        }
        distinct_dates = int(
            conn.execute(
                "SELECT COUNT(DISTINCT market_date) FROM global_market_daily_snapshot"
            ).fetchone()[0]
        )
        date_range = conn.execute(
            "SELECT MIN(market_date),MAX(market_date) FROM global_market_daily_snapshot"
        ).fetchone()
    minimum_coverage = min(coverage.values()) if coverage else 0
    minimum_required_dates = max(int(retain_market_days * 0.95), 1)
    return {
        "ok": bool(
            not failures
            and len(coverage) == len(GLOBAL_MARKET_TICKERS)
            and minimum_coverage >= minimum_required_dates
            and distinct_dates >= minimum_required_dates
        ),
        "source_quality": "supplemental",
        "ticker_count": len(coverage),
        "row_count_fetched": len(rows),
        "rows_written": written,
        "rows_pruned": pruned,
        "incomplete_session_rows_skipped": incomplete_session_rows_skipped,
        "distinct_market_dates": distinct_dates,
        "min_market_date": date_range[0] if date_range else None,
        "max_market_date": date_range[1] if date_range else None,
        "minimum_ticker_dates": minimum_coverage,
        "minimum_required_dates": minimum_required_dates,
        "coverage_by_ticker": coverage,
        "failures": failures,
    }


def materialize_taifex_night_history(
    *,
    start_date: date,
    end_date: date,
    retain_days: int = 400,
    max_workers: int = 2,
    fetcher: Callable[..., dict[str, Any]] = fetch_taifex_futures_range,
) -> dict[str, Any]:
    ranges = _month_ranges(start_date, end_date)
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 2))) as executor:
        futures = {
            executor.submit(fetcher, start_date=begin, end_date=finish): (begin, finish)
            for begin, finish in ranges
        }
        for future in as_completed(futures):
            begin, finish = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "ok": False,
                    "rows": [],
                    "start_date": begin.isoformat(),
                    "end_date": finish.isoformat(),
                    "error": str(exc),
                }
            results.append(result)

    rows = [dict(row) for result in results for row in result.get("rows") or []]
    failures = [
        {
            "start_date": str(result.get("start_date")),
            "end_date": str(result.get("end_date")),
            "reason": str(result.get("error") or result.get("status") or "empty"),
        }
        for result in results
        if not result.get("ok")
    ]
    with closing(db()) as conn:
        conn.execute("BEGIN")
        written = upsert_taifex_night_rows(conn, rows)
        pruned = prune_taifex_night_rows(conn, retain_days=retain_days)
        conn.commit()
        coverage = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                """
                SELECT contract,COUNT(DISTINCT trade_date)
                FROM taifex_night_daily_snapshot
                GROUP BY contract
                """
            ).fetchall()
        }
        distinct_dates = int(
            conn.execute(
                "SELECT COUNT(DISTINCT trade_date) FROM taifex_night_daily_snapshot"
            ).fetchone()[0]
        )
        date_range = conn.execute(
            "SELECT MIN(trade_date),MAX(trade_date) FROM taifex_night_daily_snapshot"
        ).fetchone()
    minimum_required_dates = max(int(retain_days * 0.95), 1)
    tx_dates = int(coverage.get("TX", 0))
    return {
        "ok": bool(
            not failures
            and rows
            and tx_dates >= minimum_required_dates
            and distinct_dates >= minimum_required_dates
        ),
        "source_quality": "official",
        "range_count": len(ranges),
        "row_count_fetched": len(rows),
        "rows_written": written,
        "rows_pruned": pruned,
        "distinct_trade_dates": distinct_dates,
        "min_trade_date": date_range[0] if date_range else None,
        "max_trade_date": date_range[1] if date_range else None,
        "tx_trade_dates": tx_dates,
        "minimum_required_dates": minimum_required_dates,
        "coverage_by_contract": coverage,
        "failures": failures,
    }


def _fetch_valuation_pair(
    data_date: str,
    twse_fetcher: Callable[[str], dict[str, Any]],
    tpex_fetcher: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    return {
        "data_date": data_date,
        "twse": twse_fetcher(data_date),
        "tpex": tpex_fetcher(data_date),
    }


def materialize_official_valuation_history(
    *,
    trading_dates: Iterable[str],
    retain_days: int = 200,
    max_workers: int = 4,
    twse_fetcher: Callable[[str], dict[str, Any]] = fetch_twse_daily_valuation,
    tpex_fetcher: Callable[[str], dict[str, Any]] = fetch_tpex_daily_valuation,
) -> dict[str, Any]:
    requested = sorted({str(value) for value in trading_dates if str(value)})
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 4))) as executor:
        futures = {
            executor.submit(_fetch_valuation_pair, data_date, twse_fetcher, tpex_fetcher): data_date
            for data_date in requested
        }
        for future in as_completed(futures):
            data_date = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(
                    {
                        "data_date": data_date,
                        "twse": {"ok": False, "rows": [], "error": str(exc)},
                        "tpex": {"ok": False, "rows": [], "error": str(exc)},
                    }
                )

    failures: list[dict[str, Any]] = []
    source_counts: dict[str, dict[str, int]] = {"twse": {}, "tpex": {}}
    rows_written = 0
    with closing(db()) as conn:
        conn.execute("BEGIN")
        for result in sorted(results, key=lambda item: str(item["data_date"])):
            data_date = str(result["data_date"])
            for source_name in ("twse", "tpex"):
                source_result = result[source_name]
                rows = list(source_result.get("rows") or [])
                source_counts[source_name][data_date] = len(rows)
                if not source_result.get("ok"):
                    failures.append(
                        {
                            "data_date": data_date,
                            "source": source_name,
                            "reason": str(source_result.get("error") or source_result.get("status") or "empty"),
                        }
                    )
                    continue
                rows_written += upsert_twse_daily_valuations(rows, conn=conn)
        cleanup = cleanup_twse_daily_valuation(retention_days=retain_days, conn=conn)
        conn.commit()
        coverage = {
            str(row[0]): {
                "dates": int(row[1]),
                "rows": int(row[2]),
                "min_date": row[3],
                "max_date": row[4],
            }
            for row in conn.execute(
                """
                SELECT source,COUNT(DISTINCT data_date),COUNT(*),
                       MIN(data_date),MAX(data_date)
                FROM twse_daily_valuation
                GROUP BY source
                """
            ).fetchall()
        }
    successful_pairs = sum(
        1
        for value in requested
        if source_counts["twse"].get(value, 0) >= 500
        and source_counts["tpex"].get(value, 0) >= 350
    )
    return {
        "ok": not failures and successful_pairs == len(requested),
        "source_quality": "official",
        "requested_dates": len(requested),
        "successful_date_pairs": successful_pairs,
        "rows_written": rows_written,
        "cleanup": cleanup,
        "coverage_by_source": coverage,
        "per_date_source_counts": source_counts,
        "failures": failures,
    }
