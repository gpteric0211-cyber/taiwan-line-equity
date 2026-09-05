from __future__ import annotations

"""Explicit task: record official T+1 outcomes for existing sealed predictions."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.db import assert_db_integrity, db  # noqa: E402
from services.target_outcome_materializer import materialize_available_target_outcomes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("logs/target_outcome_latest.json"))
    args = parser.parse_args()
    assert_db_integrity()
    with db() as conn:
        conn.execute("BEGIN")
        result = materialize_available_target_outcomes(conn)
        conn.commit()
    assert_db_integrity()
    report_path = args.report if args.report.is_absolute() else ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
