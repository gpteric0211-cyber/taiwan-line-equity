from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.global_market_snapshot_service import refresh_global_market_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist the bounded US-market close context used by LINE research replies.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = refresh_global_market_snapshot(dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
