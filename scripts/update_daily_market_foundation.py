from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.market_foundation_importer import run_market_foundation_update
from services.market_update_reporter import (
    append_log,
    emit_progress,
    write_daily_report,
)
from services.price_volume_daily_service import reconcile_watchlist_price_volume_after_official_update
from core.market_calendar_cache import taiwan_market_day_status


SUCCESS = 0
CLI_ERROR = 1
FATAL_ERROR = 2
SAFETY_ABORT = 3
PARTIAL_RETRYABLE = 4
SOURCE_DELAYED_RETRYABLE = 5


def classify_exit_code(result: dict) -> int:
    if result.get("refused"):
        return CLI_ERROR
    sources = result.get("official_sources") or []
    source_statuses = [
        str(item.get("status") or ("OK" if item.get("ok") is True else "UNKNOWN"))
        for item in sources
    ]
    if any(status == "SOURCE_DELAYED" for status in source_statuses):
        return SOURCE_DELAYED_RETRYABLE
    failed_sources = [
        item for item in sources
        if str(item.get("status") or ("OK" if item.get("ok") is True else "UNKNOWN")) != "OK"
    ]
    source_rows = sum(int(item.get("rows") or item.get("rows_read") or 0) for item in sources)
    if result.get("ok") and not failed_sources:
        return SUCCESS
    if sources and source_rows == 0:
        return SOURCE_DELAYED_RETRYABLE
    if result.get("status") == "PARTIAL" or failed_sources:
        return PARTIAL_RETRYABLE
    if not result.get("ok"):
        return PARTIAL_RETRYABLE
    return SUCCESS


def main() -> int:
    parser = argparse.ArgumentParser(description="Update market foundation daily data.")
    parser.add_argument("--date", dest="run_date", help="Optional target trade date YYYY-MM-DD.")
    parser.add_argument("--codes", help="Comma or whitespace separated stock codes.")
    parser.add_argument("--official-only", action="store_true", help="Use official sources only.")
    parser.add_argument("--include-scraped", action="store_true", help="Also include explicitly allowed supplemental scraped sources.")
    parser.add_argument("--allow-full-scrape", action="store_true", help="Allow scraped full-market mode.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect without DB writes.")
    parser.add_argument("--data-map", default=str(ROOT / "docs" / "DATA_FILE_MAP.txt"), help="DATA_FILE_MAP output path.")
    parser.add_argument("--report-file", default=str(ROOT / "docs" / "DAILY_UPDATE_REPORT.txt"), help="Daily report output path.")
    parser.add_argument("--log-file", help="Optional UTF-8 log output path.")
    parser.add_argument("--quiet", action="store_true", help="Suppress console progress output; log and report remain complete.")
    args = parser.parse_args()

    codes = args.codes.split() if args.codes and "," not in args.codes else args.codes
    try:
        if not args.run_date:
            today = datetime.now(ZoneInfo("Asia/Taipei")).date()
            market_day = taiwan_market_day_status(today)
            if not market_day.get("verified") or not market_day.get("is_trading_day"):
                verified = bool(market_day.get("verified"))
                clean_skip = verified and not market_day.get("is_trading_day")
                result = {
                    "ok": clean_skip,
                    "status": "CLEAN_SKIP" if clean_skip else "SOURCE_DELAYED",
                    "writes_db": False,
                    "run_date": today.isoformat(),
                    "market_day": market_day,
                    "warnings": [] if clean_skip else ["Official market calendar year is unavailable."],
                }
                exit_code = SUCCESS if clean_skip else SOURCE_DELAYED_RETRYABLE
                write_daily_report(
                    result,
                    exit_code=exit_code,
                    report_file=Path(args.report_file),
                    log_file=Path(args.log_file) if args.log_file else "",
                )
                append_log(args.log_file, result["status"], result)
                if not args.quiet:
                    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
                return exit_code
        emit_progress(
            quiet=args.quiet,
            log_file=args.log_file,
            phase_name="market_foundation",
            current_step=1,
            total_steps=5,
            current_date=args.run_date,
            current_source="start",
            status="STARTED",
        )
        result = run_market_foundation_update(
            run_date=args.run_date,
            codes=codes,
            official_only=args.official_only,
            include_scraped=args.include_scraped,
            allow_full_scrape=args.allow_full_scrape,
            dry_run=args.dry_run,
            data_map_path=Path(args.data_map),
        )
        exit_code = classify_exit_code(result)
        if exit_code == SUCCESS and not args.dry_run:
            price_volume_reconcile = reconcile_watchlist_price_volume_after_official_update(result)
            result["price_volume_reconcile"] = price_volume_reconcile
            if (
                int(price_volume_reconcile.get("candidate_count") or 0) > 0
                and not price_volume_reconcile.get("ok")
            ):
                exit_code = PARTIAL_RETRYABLE
                result.setdefault("warnings", []).append(
                    "Same-day watchlist price-volume capture was not fully reconciled; retry is allowed."
                )
        emit_progress(
            quiet=args.quiet,
            log_file=args.log_file,
            phase_name="market_foundation",
            current_step=5,
            total_steps=5,
            current_date=args.run_date,
            current_source="complete",
            fetched_rows=int(result.get("official_rows") or 0) + int(result.get("scraped_distribution_rows") or 0),
            valid_rows=int(result.get("official_rows") or 0),
            written_rows=int(result.get("official_written") or 0) + int(result.get("scraped_distribution_written") or 0),
            skipped_rows=0,
            failed_rows=1 if exit_code in (PARTIAL_RETRYABLE, SOURCE_DELAYED_RETRYABLE) else 0,
            status=str(result.get("status") or exit_code),
        )
        report_summary = write_daily_report(
            result,
            exit_code=exit_code,
            report_file=Path(args.report_file),
            log_file=Path(args.log_file) if args.log_file else "",
        )
        append_log(args.log_file, "RESULT", {"exit_code": exit_code, "report": report_summary})
        if not args.quiet:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return exit_code
    except KeyboardInterrupt:
        result = {
            "ok": False,
            "status": "SAFETY_ABORT",
            "writes_db": False,
            "warnings": ["Interrupted by user."],
        }
        write_daily_report(result, exit_code=SAFETY_ABORT, report_file=Path(args.report_file), log_file=args.log_file)
        append_log(args.log_file, "SAFETY_ABORT", {"reason": "KeyboardInterrupt"})
        return SAFETY_ABORT
    except Exception as exc:
        result = {
            "ok": False,
            "status": "FATAL_ERROR",
            "writes_db": False,
            "warnings": [str(exc)],
        }
        write_daily_report(result, exit_code=FATAL_ERROR, report_file=Path(args.report_file), log_file=args.log_file)
        append_log(args.log_file, "FATAL_ERROR", {"error": str(exc), "traceback": traceback.format_exc()})
        if not args.quiet:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return FATAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
