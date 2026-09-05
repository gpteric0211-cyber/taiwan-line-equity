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

from services.fugle_intraday_service import refresh_watchlist_intraday


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh watchlist intraday snapshots.")
    parser.add_argument("--mode", default=None, help="disabled, rest_quote_polling, websocket_candles, websocket_trades")
    parser.add_argument("--once", action="store_true", help="Run a single pass.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write DB.")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--codes", default="", help="Comma-separated stock codes; defaults to watchlist.")
    args = parser.parse_args()

    codes = [x.strip().zfill(4) for x in args.codes.split(",") if x.strip()] or None
    while True:
        result = refresh_watchlist_intraday(codes=codes, mode=args.mode, once=True, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.once:
            return 0
        time.sleep(max(10, int(args.poll_seconds or 60)))


if __name__ == "__main__":
    raise SystemExit(main())
