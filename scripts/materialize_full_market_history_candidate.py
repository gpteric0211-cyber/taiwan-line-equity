from __future__ import annotations

"""Backfill only incomplete official OHLCV dates in an isolated DB candidate."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH  # noqa: E402
from core.db import db  # noqa: E402
from services.full_market_history_service import (  # noqa: E402
    recent_verified_trading_dates,
    refresh_full_market_history_date,
)


ACTIVE_DB = (REVIEW_SRC / "data" / "taiwan50.db").resolve()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    )


def _existing_official_coverage(conn: sqlite3.Connection, trade_date: str) -> dict[str, Any]:
    expected = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM stock_master
            WHERE security_type='stock'
              AND (first_seen_date IS NULL OR first_seen_date<=?)
              AND (is_active=1 OR (last_seen_date IS NOT NULL AND last_seen_date>=?))
            """,
            (trade_date, trade_date),
        ).fetchone()[0]
    )
    official_ohlcv = int(
        conn.execute(
            """
            SELECT COUNT(DISTINCT code)
            FROM history_price
            WHERE date=? AND LOWER(COALESCE(source_quality,''))='official'
              AND close IS NOT NULL AND volume IS NOT NULL
            """,
            (trade_date,),
        ).fetchone()[0]
    )
    official_no_trade = 0
    if _table_exists(conn, "stock_verified_no_trade_date"):
        official_no_trade = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT code)
                FROM stock_verified_no_trade_date
                WHERE trade_date=? AND LOWER(COALESCE(source_quality,''))='official'
                """,
                (trade_date,),
            ).fetchone()[0]
        )
    observed = official_ohlcv + official_no_trade
    coverage = observed / expected if expected else 0.0
    return {
        "trade_date": trade_date,
        "expected_codes": expected,
        "official_ohlcv_codes": official_ohlcv,
        "official_no_trade_codes": official_no_trade,
        "coverage": coverage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Candidate-only, resumable official full-market OHLCV gap fill."
    )
    parser.add_argument("--days", type=int, default=600)
    parser.add_argument("--end-date")
    parser.add_argument("--minimum-local-coverage", type=float, default=0.95)
    parser.add_argument(
        "--calendar-buffer-days",
        type=int,
        default=30,
        help="Extra scheduled dates used to replace historical emergency closures.",
    )
    parser.add_argument("--max-dates-per-run", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "docs" / "FULL_MARKET_HISTORY_GAP_FILL_REPORT.json",
    )
    args = parser.parse_args()

    candidate_db = Path(DB_PATH).resolve()
    if not args.dry_run and candidate_db == ACTIVE_DB:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "refused_active_database",
                    "database": str(candidate_db),
                    "reason": "run through the isolated candidate publisher",
                },
                ensure_ascii=False,
            )
        )
        return 2

    requested_days = max(int(args.days), 1)
    candidate_dates = recent_verified_trading_dates(
        days=requested_days + max(int(args.calendar_buffer_days), 0),
        end_date=args.end_date,
    )
    with db() as conn:
        coverage_before = [
            _existing_official_coverage(conn, trade_date) for trade_date in candidate_dates
        ]
    complete_dates = {
        item["trade_date"]
        for item in coverage_before
        if float(item["coverage"]) >= float(args.minimum_local_coverage)
    }
    incomplete = [
        item["trade_date"]
        for item in coverage_before
        if float(item["coverage"]) < float(args.minimum_local_coverage)
    ]
    selected: list[str] = []
    selection_limit = max(int(args.max_dates_per_run), 0)
    for trade_date in incomplete:
        if len(complete_dates) >= requested_days:
            break
        if selection_limit and len(selected) >= selection_limit:
            break
        selected.append(trade_date)

    results: list[dict[str, Any]] = []
    historical_non_sessions: list[dict[str, Any]] = []
    for index, trade_date in enumerate(selected, start=1):
        item = refresh_full_market_history_date(trade_date, dry_run=args.dry_run)
        results.append(item)
        source_results = list(item.get("source_results") or [])
        no_official_rows = bool(source_results) and all(
            int(source.get("observed_codes") or 0) == 0 for source in source_results
        )
        if item.get("storage_allowed"):
            complete_dates.add(trade_date)
        elif no_official_rows:
            historical_non_sessions.append(
                {
                    "trade_date": trade_date,
                    "classification": "no_exact_official_daily_report_on_either_market",
                    "source_results": source_results,
                }
            )
        print(
            json.dumps(
                {
                    "progress": f"{index}/{len(selected)}",
                    "trade_date": trade_date,
                    "storage_allowed": item.get("storage_allowed"),
                    "batch_status": item.get("batch_status"),
                    "rows_written": item.get("rows_written"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        if len(complete_dates) >= requested_days:
            break

    with db() as conn:
        coverage_after = [
            _existing_official_coverage(conn, trade_date) for trade_date in candidate_dates
        ]
    complete_after = [
        item
        for item in coverage_after
        if float(item["coverage"]) >= float(args.minimum_local_coverage)
    ]
    final_target_dates = [item["trade_date"] for item in complete_after[:requested_days]]
    remaining_count = max(requested_days - len(final_target_dates), 0)
    non_session_dates = {item["trade_date"] for item in historical_non_sessions}
    fetch_failures = [
        item
        for item in results
        if not item.get("storage_allowed")
        and str(item.get("trade_date") or "") not in non_session_dates
    ]
    report = {
        "ok": not fetch_failures and remaining_count == 0,
        "status": (
            "ok"
            if not fetch_failures and remaining_count == 0
            else "source_failure" if fetch_failures else "insufficient_verified_sessions"
        ),
        "database": str(candidate_db),
        "dry_run": bool(args.dry_run),
        "target_trading_days": requested_days,
        "calendar_candidate_days": len(candidate_dates),
        "minimum_local_coverage": float(args.minimum_local_coverage),
        "incomplete_dates_before": len(incomplete),
        "selected_dates": len(selected),
        "verified_session_dates_after": len(final_target_dates),
        "remaining_verified_session_dates": remaining_count,
        "earliest_verified_target_date": final_target_dates[-1] if final_target_dates else None,
        "rows_written": sum(int(item.get("rows_written") or 0) for item in results),
        "fetch_failure_count": len(fetch_failures),
        "historical_non_session_count": len(historical_non_sessions),
        "historical_non_sessions": historical_non_sessions,
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "results"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
