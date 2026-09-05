from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.db import assert_db_integrity, db  # noqa: E402
from services.tdcc_equity_concentration_service import (  # noqa: E402
    rebuild_tdcc_equity_summaries_from_persisted_distribution,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild all persisted TDCC summaries without network access."
    )
    parser.add_argument("--codes", help="Optional comma/space-separated stock codes.")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    codes = None
    if args.codes:
        codes = [item for item in args.codes.replace(",", " ").split() if item]
    assert_db_integrity(full=True)
    with db() as conn:
        result = rebuild_tdcc_equity_summaries_from_persisted_distribution(
            conn,
            codes,
        )
        conn.commit()
    assert_db_integrity(full=True)
    payload = {"ok": True, **result}
    if args.report:
        report = args.report if args.report.is_absolute() else ROOT / args.report
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
