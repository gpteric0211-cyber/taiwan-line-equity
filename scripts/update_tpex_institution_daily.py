from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.tpex_institution import fetch_tpex_institution_dry_run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run official TPEx institutional daily data.")
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.add_argument("--max-requests", type=int, default=10)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true")
    group.add_argument("--date")
    args = parser.parse_args()
    result = fetch_tpex_institution_dry_run(None if args.latest else args.date, max_requests=args.max_requests)
    result["dry_run"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
