from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.full_market_history_service import (  # noqa: E402
    recent_verified_trading_dates,
    refresh_full_market_history_date,
)


DEFAULT_STATE = ROOT / "logs" / "market_foundation" / "full_market_history_state.json"
DEFAULT_REPORT = ROOT / "docs" / "FULL_MARKET_HISTORY_BACKFILL_REPORT.json"


def _load_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, value: dict, *, backup_existing: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup_existing and path.exists():
        backup_path = path.with_suffix(path.suffix + ".bak")
        backup_temporary = backup_path.with_suffix(backup_path.suffix + ".tmp")
        shutil.copy2(path, backup_temporary)
        backup_temporary.replace(backup_path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable official full-market OHLCV backfill by trade date.")
    parser.add_argument("--days", type=int, default=600)
    parser.add_argument("--end-date")
    parser.add_argument("--max-dates-per-run", type=int, default=20)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    dates = recent_verified_trading_dates(days=max(args.days, 1), end_date=args.end_date)
    state = _load_state(args.state) if args.resume else {}
    completed = set(state.get("completed_dates") or [])
    selected = [value for value in dates if value not in completed][: max(args.max_dates_per_run, 0)]
    results: list[dict] = []
    for index, trade_date in enumerate(selected, start=1):
        item = refresh_full_market_history_date(trade_date, dry_run=args.dry_run)
        results.append(item)
        if item.get("ok"):
            completed.add(trade_date)
        if not args.quiet:
            print(
                json.dumps(
                    {
                        "progress": f"{index}/{len(selected)}",
                        "trade_date": trade_date,
                        "status": item.get("status"),
                        "rows_written": item.get("rows_written"),
                        "sources": item.get("source_results"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if args.resume and not args.dry_run:
            _write_json(
                args.state,
                {
                    "completed_dates": sorted(completed, reverse=True),
                    "target_dates": dates,
                    "remaining_dates": [value for value in dates if value not in completed],
                },
                backup_existing=True,
            )
    report = {
        "ok": bool(selected) and all(item.get("ok") for item in results),
        "dry_run": args.dry_run,
        "target_day_count": len(dates),
        "selected_day_count": len(selected),
        "completed_day_count": len(completed),
        "remaining_day_count": len([value for value in dates if value not in completed]),
        "rows_written": sum(int(item.get("rows_written") or 0) for item in results),
        "no_trade_rows_written": sum(int(item.get("no_trade_rows_written") or 0) for item in results),
        "results": results,
    }
    _write_json(args.report, report)
    print(json.dumps({key: report[key] for key in report if key != "results"}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
