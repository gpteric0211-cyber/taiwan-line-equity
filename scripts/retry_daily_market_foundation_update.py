from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.market_update_reporter import append_log, ensure_market_log_dirs, write_daily_report


RETRYABLE_CODES = {4, 5}


def parse_hhmm(value: str | None) -> dt_time | None:
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def default_log_file() -> Path:
    return ROOT / "logs" / "market_foundation" / f"daily_update_{datetime.now().strftime('%Y-%m-%d')}.log"


def after_window(end: dt_time | None) -> bool:
    if end is None:
        return False
    return datetime.now().time() > end


def build_update_command(args: argparse.Namespace, log_file: Path, report_file: Path) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "update_daily_market_foundation.py"),
        "--report-file",
        str(report_file),
        "--log-file",
        str(log_file),
    ]
    if args.official_only:
        cmd.append("--official-only")
    if args.run_date:
        cmd.extend(["--date", args.run_date])
    if args.codes:
        cmd.extend(["--codes", args.codes])
    if args.dry_run:
        cmd.append("--dry-run")
    if args.quiet:
        cmd.append("--quiet")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Retry daily market foundation updates based on exit code only.")
    parser.add_argument("--official-only", action="store_true", help="Use official sources only.")
    parser.add_argument("--date", dest="run_date", help="Optional target trade date YYYY-MM-DD.")
    parser.add_argument("--codes", help="Comma or whitespace separated stock codes.")
    parser.add_argument("--max-retries", type=int, default=18, help="Maximum retry count after the first attempt.")
    parser.add_argument("--retry-delay-seconds", type=int, default=1800, help="Seconds to wait between retryable attempts.")
    parser.add_argument("--time-window-start", default="15:00", help="Informational start time HH:MM; does not block the first attempt.")
    parser.add_argument("--time-window-end", default="23:59", help="Hard retry stop time HH:MM.")
    parser.add_argument("--log-file", help="UTF-8 log output path.")
    parser.add_argument("--report-file", default=str(ROOT / "docs" / "DAILY_UPDATE_REPORT.txt"), help="Daily report output path.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect without DB writes.")
    parser.add_argument("--quiet", action="store_true", help="Suppress console progress output.")
    args = parser.parse_args()

    ensure_market_log_dirs()
    log_file = Path(args.log_file) if args.log_file else default_log_file()
    report_file = Path(args.report_file)
    start = parse_hhmm(args.time_window_start)
    end = parse_hhmm(args.time_window_end)
    if start and datetime.now().time() < start:
        append_log(log_file, "EARLY_MANUAL_START", {"time_window_start": args.time_window_start})
    if after_window(end):
        result = {
            "ok": False,
            "status": "TIME_WINDOW_EXCEEDED",
            "writes_db": False,
            "warnings": [f"Current time is later than retry window end {args.time_window_end}."],
        }
        write_daily_report(result, exit_code=5, report_file=report_file, log_file=log_file)
        append_log(log_file, "TIME_WINDOW_EXCEEDED", {"time_window_end": args.time_window_end, "attempts": 0})
        if not args.quiet:
            print(f"TIME_WINDOW_EXCEEDED | window ended {args.time_window_end}")
        return 5

    cmd = build_update_command(args, log_file, report_file)
    retry_count = 0
    last_code = 2
    while True:
        if retry_count > 0 and after_window(end):
            append_log(log_file, "TIME_WINDOW_EXCEEDED", {"last_exit_code": last_code, "time_window_end": args.time_window_end})
            return last_code if last_code in RETRYABLE_CODES else 2

        attempt_payload = {
            "attempt_number": retry_count + 1,
            "retry_count": retry_count,
            "max_retries": args.max_retries,
            "time_window_start": args.time_window_start,
            "time_window_end": args.time_window_end,
            "command": cmd,
        }
        append_log(log_file, "ATTEMPT_START", attempt_payload)
        if not args.quiet:
            print(f"Attempt {retry_count}/{args.max_retries} | status=STARTED | window ends {args.time_window_end}")
            sys.stdout.flush()
        completed = subprocess.run(cmd, cwd=ROOT)
        last_code = int(completed.returncode)
        append_log(log_file, "ATTEMPT_END", {"exit_code": last_code, "retry_count": retry_count})

        if last_code not in RETRYABLE_CODES:
            return last_code
        if retry_count >= args.max_retries:
            append_log(log_file, "MAX_RETRIES_EXHAUSTED", {"last_exit_code": last_code, "max_retries": args.max_retries})
            return last_code
        if after_window(end):
            append_log(log_file, "TIME_WINDOW_EXCEEDED", {"last_exit_code": last_code, "time_window_end": args.time_window_end})
            return last_code

        retry_count += 1
        remaining = max(args.max_retries - retry_count, 0)
        append_log(log_file, "RETRY_WAIT", {
            "retry_count": retry_count,
            "remaining_retries": remaining,
            "previous_exit_code": last_code,
            "next_retry_wait_seconds": args.retry_delay_seconds,
            "time_window_start": args.time_window_start,
            "time_window_end": args.time_window_end,
        })
        if not args.quiet:
            status = "PARTIAL" if last_code == 4 else "SOURCE_DELAYED"
            print(f"Attempt {retry_count}/{args.max_retries} | status={status} | next retry in {args.retry_delay_seconds}s | window ends {args.time_window_end}")
        time.sleep(max(args.retry_delay_seconds, 0))


if __name__ == "__main__":
    raise SystemExit(main())
