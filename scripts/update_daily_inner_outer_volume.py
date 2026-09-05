from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.components import read_components  # noqa: E402
from core.db import init_db  # noqa: E402
from repository.watchlist_repository import get_watchlist_codes  # noqa: E402
from services.inner_outer_accumulation_service import refresh_daily_inner_outer_volume_for_codes  # noqa: E402


def _codes_from_args(args: argparse.Namespace) -> list[str]:
    if args.codes:
        return [part.strip() for part in str(args.codes).split(",") if part.strip()]
    if args.mode == "tw50":
        return [str(item.get("code") or "").strip() for item in read_components()]
    if args.mode == "watchlist":
        return get_watchlist_codes()
    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare daily inner/outer volume accumulation signals when an authorized source is available."
    )
    parser.add_argument("--codes", default="", help="Comma-separated stock codes.")
    parser.add_argument("--mode", choices=["watchlist", "tw50"], default="watchlist")
    parser.add_argument("--source", default="none", help="Authorized source name. Current default writes no rows.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned work without DB writes.")
    parser.add_argument("--apply", action="store_true", help="Reserved for authorized source imports; currently writes no rows.")
    args = parser.parse_args()

    init_db()
    codes = _codes_from_args(args)
    result = refresh_daily_inner_outer_volume_for_codes(
        codes,
        source=str(args.source or "none"),
        dry_run=not bool(args.apply),
    )
    result["mode"] = args.mode
    result["requested_apply"] = bool(args.apply)
    result["dry_run"] = bool(args.dry_run or not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
