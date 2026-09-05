from __future__ import annotations

"""Backfill official margin/short/lending history in an isolated DB candidate."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH, default_portable_db_path  # noqa: E402
from core.db import assert_db_integrity, db  # noqa: E402
from services.full_market_history_service import (  # noqa: E402
    recent_verified_trading_dates,
    refresh_full_market_history_date,
)
from services.official_credit_balance_service import refresh_official_credit_balances  # noqa: E402


def _existing_dates(minimum_rows: int) -> set[str]:
    with db() as conn:
        return {
            str(row[0])
            for row in conn.execute(
                """
                SELECT trade_date FROM credit_balance_daily
                GROUP BY trade_date HAVING COUNT(*)>=?
                """,
                (minimum_rows,),
            ).fetchall()
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate-only official credit history backfill.")
    parser.add_argument("--days", type=int, default=900)
    parser.add_argument("--calendar-buffer-days", type=int, default=40)
    parser.add_argument("--minimum-date-rows", type=int, default=1000)
    parser.add_argument("--max-dates-per-run", type=int, default=0)
    parser.add_argument("--lock-wait-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if DB_PATH.resolve() == default_portable_db_path().resolve():
        raise SystemExit("refusing direct active-DB write; run through isolated publisher")

    # The isolated publisher performs full integrity checks before and after
    # this child pipeline.  Keep child checks fast without weakening publish.
    assert_db_integrity()
    requested_days = max(int(args.days), 1)
    candidate_dates = recent_verified_trading_dates(
        days=requested_days + max(int(args.calendar_buffer_days), 0)
    )
    complete = _existing_dates(max(int(args.minimum_date_rows), 1))
    initial_latest_complete = max(complete) if complete else None
    selected = [value for value in candidate_dates if value not in complete]
    if int(args.max_dates_per_run) > 0:
        selected = selected[: int(args.max_dates_per_run)]

    results: list[dict[str, object]] = []
    non_sessions: list[str] = []
    expected_source_delays: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for index, trade_date in enumerate(selected, start=1):
        if len(complete.intersection(candidate_dates)) >= requested_days:
            break
        result = refresh_official_credit_balances(trade_date, dry_run=False)
        summary = {
            "trade_date": trade_date,
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "row_count": int(result.get("row_count") or 0),
            "rows_written": int(result.get("rows_written") or 0),
            "listed_rows": int(result.get("listed_rows") or 0),
            "otc_rows": int(result.get("otc_rows") or 0),
            "excluded_formula_rows": int(result.get("excluded_formula_rows") or 0),
            "excluded_formula_examples": list(result.get("excluded_formula_examples") or []),
        }
        results.append(summary)
        if result.get("ok") and int(result.get("row_count") or 0) >= int(args.minimum_date_rows):
            complete.add(trade_date)
        elif (
            str(result.get("status") or "") == "source_delayed"
            and initial_latest_complete is not None
            and trade_date > initial_latest_complete
        ):
            expected_source_delays.append(
                {
                    "trade_date": trade_date,
                    "status": "expected_latest_source_delay",
                    "row_count": int(result.get("row_count") or 0),
                    "initial_latest_complete": initial_latest_complete,
                }
            )
        elif int(result.get("row_count") or 0) == 0:
            market_probe = refresh_full_market_history_date(trade_date, dry_run=True)
            sources = list(market_probe.get("source_results") or [])
            no_market_rows = bool(sources) and all(
                int(source.get("observed_codes") or 0) == 0 for source in sources
            )
            if no_market_rows:
                non_sessions.append(trade_date)
            else:
                failures.append({
                    "trade_date": trade_date,
                    "reason": "credit_source_empty_on_confirmed_market_session",
                    "result": result,
                    "official_market_probe": sources,
                })
        else:
            failures.append({"trade_date": trade_date, "result": result})
        print(
            json.dumps(
                {
                    "progress": f"{index}/{len(selected)}",
                    **summary,
                    "verified_dates": len(complete.intersection(candidate_dates)),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    verified_dates = sorted(complete.intersection(candidate_dates), reverse=True)[:requested_days]
    publishable = len(verified_dates) == requested_days and not failures
    report = {
        "ok": publishable,
        "complete": publishable,
        "status": "ok" if publishable else "failed",
        "target_days": requested_days,
        "verified_days": len(verified_dates),
        "earliest_verified_date": verified_dates[-1] if verified_dates else None,
        "source_failure_count": len(failures),
        "source_failures": failures,
        "formula_exception_count": sum(
            int(value.get("excluded_formula_rows") or 0) for value in results
        ),
        "formula_exception_dates": [
            {
                "trade_date": value["trade_date"],
                "excluded_formula_rows": value["excluded_formula_rows"],
                "examples": value["excluded_formula_examples"],
            }
            for value in results
            if int(value.get("excluded_formula_rows") or 0) > 0
        ],
        "expected_latest_source_delays": expected_source_delays,
        "historical_non_session_dates": non_sessions,
        "results": results,
    }
    report_path = args.report if args.report.is_absolute() else ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    assert_db_integrity()
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False, indent=2))
    return 0 if publishable else 4


if __name__ == "__main__":
    raise SystemExit(main())
