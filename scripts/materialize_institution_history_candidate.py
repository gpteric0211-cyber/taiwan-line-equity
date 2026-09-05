from __future__ import annotations

"""Backfill official institution activity in a rollback-safe DB candidate."""

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
from services.institution_snapshot_service import refresh_official_institution_snapshot  # noqa: E402


def _existing_dates(minimum_rows: int) -> set[str]:
    with db() as conn:
        return {
            str(row[0])
            for row in conn.execute(
                """
                SELECT trade_date FROM institution_activity_daily
                GROUP BY trade_date HAVING COUNT(*)>=?
                """,
                (minimum_rows,),
            ).fetchall()
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Candidate-only institution history backfill.")
    parser.add_argument("--days", type=int, default=720)
    parser.add_argument("--calendar-buffer-days", type=int, default=40)
    parser.add_argument("--minimum-date-rows", type=int, default=1000)
    parser.add_argument("--max-dates-per-run", type=int, default=0)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if DB_PATH.resolve() == default_portable_db_path().resolve():
        raise SystemExit("refusing direct active-DB write; run through isolated publisher")

    assert_db_integrity(full=True)
    requested_days = max(int(args.days), 1)
    candidate_dates = recent_verified_trading_dates(
        days=requested_days + max(int(args.calendar_buffer_days), 0)
    )
    complete = _existing_dates(max(int(args.minimum_date_rows), 1))
    selected = [date for date in candidate_dates if date not in complete]
    if int(args.max_dates_per_run) > 0:
        selected = selected[: int(args.max_dates_per_run)]
    results = []
    non_sessions = []
    source_gaps = []
    failures = []
    for index, trade_date in enumerate(selected, start=1):
        if len(complete.intersection(candidate_dates)) >= requested_days:
            break
        result = refresh_official_institution_snapshot(trade_date=trade_date, dry_run=False)
        summary = {
            "trade_date": trade_date,
            "ok": bool(result.get("ok")),
            "status": result.get("status"),
            "row_count": int(result.get("row_count") or 0),
            "rows_written": int(result.get("rows_written") or 0),
        }
        results.append(summary)
        if result.get("ok") and int(result.get("row_count") or 0) >= int(args.minimum_date_rows):
            complete.add(trade_date)
        elif result.get("status") == "source_delayed" and not result.get("source_dates"):
            market_probe = refresh_full_market_history_date(trade_date, dry_run=True)
            probe_sources = list(market_probe.get("source_results") or [])
            no_market_rows = bool(probe_sources) and all(
                int(source.get("observed_codes") or 0) == 0 for source in probe_sources
            )
            if no_market_rows:
                non_sessions.append(trade_date)
            elif market_probe.get("storage_allowed"):
                source_gaps.append(
                    {
                        "trade_date": trade_date,
                        "reason": "institution_source_empty_on_confirmed_market_session",
                        "official_market_probe": probe_sources,
                    }
                )
            else:
                failures.append(
                    {
                        "trade_date": trade_date,
                        "result": result,
                        "official_market_probe": market_probe,
                    }
                )
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
        "complete": publishable and not source_gaps,
        "status": "ok" if publishable and not source_gaps else "partial_source_gaps" if publishable else "failed",
        "target_days": requested_days,
        "verified_days": len(verified_dates),
        "earliest_verified_date": verified_dates[-1] if verified_dates else None,
        "source_failure_count": len(failures),
        "confirmed_market_session_source_gap_count": len(source_gaps),
        "confirmed_market_session_source_gaps": source_gaps,
        "historical_non_session_dates": non_sessions,
        "results": results,
    }
    report_path = args.report if args.report.is_absolute() else ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    assert_db_integrity(full=True)
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False, indent=2))
    return 0 if publishable else 4


if __name__ == "__main__":
    raise SystemExit(main())
