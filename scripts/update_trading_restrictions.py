from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.trading_restriction_service import refresh_official_trading_restrictions  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh official TWSE/TPEx attention, disposition, and trading-mode snapshots."
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--as-of-date", help="Decision data date in YYYY-MM-DD; defaults to latest completed market date.")
    args = parser.parse_args()
    result = refresh_official_trading_restrictions(
        dry_run=args.dry_run,
        as_of_date=args.as_of_date,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
