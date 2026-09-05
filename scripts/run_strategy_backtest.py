from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.strategy_backtest_service import (  # noqa: E402
    render_strategy_backtest_markdown,
    run_strategy_backtest,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only point-in-time validation of screening and low-zone rules."
    )
    parser.add_argument("--database", type=Path, help="Optional SQLite path; defaults to configured project DB.")
    parser.add_argument("--date-from", help="Optional signal start date YYYY-MM-DD.")
    parser.add_argument("--date-to", help="Optional data end date YYYY-MM-DD.")
    parser.add_argument("--codes", help="Optional comma/space-separated four-digit stock codes.")
    parser.add_argument(
        "--json-report",
        type=Path,
        default=ROOT / "docs" / "STRATEGY_BACKTEST_BASELINE.json",
    )
    parser.add_argument(
        "--markdown-report",
        type=Path,
        default=ROOT / "docs" / "STRATEGY_BACKTEST_BASELINE.md",
    )
    args = parser.parse_args()
    codes = (
        [item for item in args.codes.replace(",", " ").split() if item]
        if args.codes
        else None
    )
    report = run_strategy_backtest(
        database_path=args.database,
        date_from=args.date_from,
        date_to=args.date_to,
        codes=codes,
    )
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.markdown_report.write_text(
        render_strategy_backtest_markdown(report),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "version": report.get("version"),
                "raw_rows": (report.get("input_data") or {}).get("raw_rows"),
                "coverage": report.get("coverage"),
                "low_zone_rows": (report.get("low_zone_validation") or {}).get(
                    "evaluated_low_rsi_rows"
                ),
                "json_report": str(args.json_report),
                "markdown_report": str(args.markdown_report),
                "writes_database": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
