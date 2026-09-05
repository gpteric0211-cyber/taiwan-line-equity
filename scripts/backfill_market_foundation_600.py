from __future__ import annotations

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

from core.config import DB_PATH
from core.db import db
from services.market_foundation_importer import run_market_foundation_update
from services.market_update_reporter import append_log, emit_progress, ensure_market_log_dirs, write_backfill_report


STATE_FILE = ROOT / "logs" / "market_foundation" / "backfill_state.json"
STATE_BAK = ROOT / "logs" / "market_foundation" / "backfill_state.bak.json"


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"completed_dates": [], "partial_dates": [], "failed_dates": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"completed_dates": [], "partial_dates": [], "failed_dates": []}


def write_state_atomic(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if STATE_FILE.exists():
        STATE_FILE.replace(STATE_BAK)
    tmp.replace(STATE_FILE)


def existing_trading_dates(limit: int) -> tuple[list[str], str]:
    if not DB_PATH.exists():
        return [], "db_missing"
    try:
        with db() as conn:
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='history_price'"
            ).fetchone()
            if not table:
                return [], "history_price_missing"
            rows = conn.execute(
                "SELECT DISTINCT date FROM history_price WHERE date IS NOT NULL AND date<>'' ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [str(row["date"]) for row in rows], "history_price_distinct_dates"
    except sqlite3.Error:
        return [], "db_unavailable"


def batch_verify_completed(dates: list[str]) -> set[str]:
    if not dates or not DB_PATH.exists():
        return set()
    placeholders = ",".join("?" for _ in dates)
    with db() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT date FROM history_price WHERE date IN ({placeholders}) AND close IS NOT NULL",
            dates,
        ).fetchall()
    return {str(row["date"]) for row in rows}


def summarize(
    *,
    target_dates: list[str],
    completed: set[str],
    partial: set[str],
    failed: set[str],
    skipped: set[str],
    date_source: str,
    dry_run: bool,
    writes_db: bool,
    log_file: Path | None,
) -> dict[str, Any]:
    target = len(target_dates)
    completed_count = len(completed)
    return {
        "overall_status": "SKIPPED" if date_source != "history_price_distinct_dates" else ("DRY_RUN" if dry_run else "OK"),
        "target_trading_days": target,
        "completed_trading_days": completed_count,
        "completed_pct": round((completed_count / target) * 100, 2) if target else 0,
        "partial_dates_count": len(partial),
        "failed_dates_count": len(failed),
        "skipped_dates_count": len(skipped),
        "remaining_dates_count": max(target - completed_count - len(partial) - len(failed) - len(skipped), 0),
        "current_batch_range": f"{target_dates[-1]}..{target_dates[0]}" if target_dates else "",
        "last_success_date": max(completed) if completed else "",
        "date_source": date_source,
        "data_completeness_status": "NO_BASE_DATES" if not target_dates else "PARTIAL" if partial or failed else "OK",
        "writes_db": writes_db,
        "dry_run": dry_run,
        "log_file": str(log_file or ""),
        "state_file": str(STATE_FILE),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill latest official market foundation dates from local date inventory.")
    parser.add_argument("--days", type=int, default=600, help="Target latest trading-day count.")
    parser.add_argument("--official-only", action="store_true", help="Official-only mode. Supplemental sources are not supported here.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect without DB writes or formal state updates.")
    parser.add_argument("--resume", action="store_true", help="Resume from logs/market_foundation/backfill_state.json.")
    parser.add_argument("--max-dates-per-run", type=int, default=25, help="Maximum dates to process in this run.")
    parser.add_argument("--no-prune", action="store_true", help="Accepted for operational clarity; importer pruning remains disabled only by dry-run.")
    parser.add_argument("--report-file", default=str(ROOT / "docs" / "BACKFILL_UPDATE_REPORT.txt"), help="Backfill report path.")
    parser.add_argument("--log-file", help="UTF-8 log output path.")
    parser.add_argument("--quiet", action="store_true", help="Suppress console progress output.")
    args = parser.parse_args()

    ensure_market_log_dirs()
    log_file = Path(args.log_file) if args.log_file else ROOT / "logs" / "market_foundation" / "backfill_latest.log"
    target_dates, date_source = existing_trading_dates(max(args.days, 1))
    if date_source != "history_price_distinct_dates" or not target_dates:
        summary = summarize(
            target_dates=[],
            completed=set(),
            partial=set(),
            failed=set(),
            skipped=set(),
            date_source=date_source,
            dry_run=args.dry_run,
            writes_db=False,
            log_file=log_file,
        )
        write_backfill_report(summary, report_file=Path(args.report_file))
        append_log(log_file, "BACKFILL_SKIPPED", summary)
        if not args.quiet:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    state = load_state() if args.resume and not args.dry_run else {"completed_dates": [], "partial_dates": [], "failed_dates": []}
    completed_dates = set(state.get("completed_dates") or [])
    completed_dates.update(batch_verify_completed(target_dates))
    partial_dates = set(state.get("partial_dates") or [])
    failed_dates = set(state.get("failed_dates") or [])
    pending = [d for d in target_dates if d not in completed_dates]
    selected = pending[: max(args.max_dates_per_run, 0)]
    writes_db = False
    skipped_dates: set[str] = set()

    total_steps = max(len(selected), 1)
    for idx, trade_date in enumerate(selected, start=1):
        emit_progress(
            quiet=args.quiet,
            log_file=log_file,
            phase_name="backfill",
            current_step=idx,
            total_steps=total_steps,
            current_date=trade_date,
            current_source="official",
            status="STARTED",
        )
        result = run_market_foundation_update(
            run_date=trade_date,
            official_only=True,
            include_scraped=False,
            allow_full_scrape=False,
            dry_run=args.dry_run,
            generate_data_map=False,
            prune=not args.no_prune,
        )
        if result.get("official_rows"):
            completed_dates.add(trade_date)
            partial_dates.discard(trade_date)
            failed_dates.discard(trade_date)
        elif result.get("ok") is False:
            partial_dates.add(trade_date)
        else:
            skipped_dates.add(trade_date)
        writes_db = writes_db or bool(result.get("writes_db"))
        append_log(log_file, "BACKFILL_DATE_RESULT", {"date": trade_date, "result": result})

    if args.resume and not args.dry_run:
        state = {
            "completed_dates": sorted(completed_dates),
            "partial_dates": sorted(partial_dates),
            "failed_dates": sorted(failed_dates),
            "target_dates": target_dates,
            "date_source": date_source,
        }
        write_state_atomic(state)

    summary = summarize(
        target_dates=target_dates,
        completed=completed_dates,
        partial=partial_dates,
        failed=failed_dates,
        skipped=skipped_dates,
        date_source=date_source,
        dry_run=args.dry_run,
        writes_db=writes_db,
        log_file=log_file,
    )
    write_backfill_report(summary, report_file=Path(args.report_file))
    append_log(log_file, "BACKFILL_SUMMARY", summary)
    if not args.quiet:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
