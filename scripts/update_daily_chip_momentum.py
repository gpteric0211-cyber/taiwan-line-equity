from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "review_src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.components import read_components  # noqa: E402
from repository.watchlist_repository import get_watchlist_codes  # noqa: E402
from services.daily_chip_momentum_service import refresh_daily_chip_momentum_for_codes  # noqa: E402


def _codes_from_args(args: argparse.Namespace) -> list[str]:
    if args.codes:
        return [x.strip().zfill(4)[:4] for x in args.codes.split(",") if x.strip()]
    if args.mode == "tw50":
        return [str(item.get("code") or "").zfill(4)[:4] for item in read_components()]
    return get_watchlist_codes()


def main() -> int:
    parser = argparse.ArgumentParser(description="Update daily chip momentum from local official data.")
    parser.add_argument("--mode", choices=["watchlist", "tw50"], default="watchlist")
    parser.add_argument("--date")
    parser.add_argument("--retry", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--codes", default="")
    parser.add_argument("--retry-wait-seconds", type=int, default=900)
    args = parser.parse_args()
    codes = _codes_from_args(args)
    attempts = 1 if args.once else max(1, int(args.retry or 1))
    last_result = None
    for attempt in range(1, attempts + 1):
        last_result = refresh_daily_chip_momentum_for_codes(codes, date=args.date, mode=args.mode, dry_run=args.dry_run)
        if not last_result.get("failed"):
            break
        if attempt < attempts:
            time.sleep(max(1, int(args.retry_wait_seconds)))
    print(json.dumps(last_result or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
