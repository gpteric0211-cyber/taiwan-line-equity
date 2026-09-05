from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

def _run_pipeline(argv: list[str]) -> int:
    """Delegate to the canonical finalize stage and its shared process lock."""

    from scripts.run_post_close_daily_pipeline import main as pipeline_main

    return int(pipeline_main(argv))


def main() -> int:
    parser = argparse.ArgumentParser(description="Retry the post-close database update without recapturing Fugle trades.")
    parser.add_argument("--date", dest="trade_date", help="Target trading date YYYY-MM-DD.")
    parser.add_argument("--max-retries", type=int, default=18)
    parser.add_argument("--retry-delay-seconds", type=int, default=1800)
    parser.add_argument("--window-end", default="23:59")
    parser.add_argument("--lock-wait-seconds", type=int, default=0)
    args = parser.parse_args()

    pipeline_args = [
        "--stage",
        "finalize",
        "--max-retries",
        str(args.max_retries),
        "--retry-delay-seconds",
        str(args.retry_delay_seconds),
        "--window-end",
        str(args.window_end),
        "--lock-wait-seconds",
        str(args.lock_wait_seconds),
    ]
    if args.trade_date:
        pipeline_args.extend(["--date", str(args.trade_date)])
    return _run_pipeline(pipeline_args)


if __name__ == "__main__":
    raise SystemExit(main())
