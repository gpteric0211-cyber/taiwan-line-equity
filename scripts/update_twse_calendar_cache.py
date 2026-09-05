from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.twse_calendar import refresh_twse_holiday_cache  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh one official TWSE annual holiday schedule into the merged local cache."
    )
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = refresh_twse_holiday_cache(
        required_year=args.year,
        write_cache=not args.dry_run,
    )
    summary = {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "required_year": args.year,
        "years": result.get("years"),
        "closure_count": result.get("closure_count"),
        "cache_written": bool(result.get("cache_written")),
        "dry_run": args.dry_run,
        "error": result.get("error"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
