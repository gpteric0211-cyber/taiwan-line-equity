from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.db import init_db  # noqa: E402
from services.twse_valuation_service import update_twse_daily_valuation  # noqa: E402


def result_exit_code(result: dict) -> int:
    if result.get("ok"):
        return 0
    if str(result.get("status") or "").lower() == "source_delayed":
        return 5
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Update official TWSE BWIBBU valuation data.")
    parser.add_argument("--data-date", help="Trading date in YYYY-MM-DD format.")
    parser.add_argument("--retention-days", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_db()
    result = update_twse_daily_valuation(
        data_date=args.data_date,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
