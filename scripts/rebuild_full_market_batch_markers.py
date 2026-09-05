from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH  # noqa: E402
from core.full_market_batch_schema import ensure_full_market_batch_schema  # noqa: E402
from repository.full_market_batch_repository import (  # noqa: E402
    evaluate_full_market_batch,
    record_full_market_batch,
)


def _connect(path: Path, *, read_only: bool) -> sqlite3.Connection:
    target = path.resolve()
    if read_only:
        conn = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True, timeout=30)
        conn.execute("PRAGMA query_only=ON")
    else:
        conn = sqlite3.connect(target, timeout=30)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
    conn.row_factory = sqlite3.Row
    return conn


def _report_view(evaluation: dict) -> dict:
    result = dict(evaluation)
    for field in ("unclassified_codes", "overlap_codes"):
        codes = list(result.pop(field, []) or [])
        result[f"{field}_sample"] = codes[:20]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit or explicitly backfill full-market batch completion markers."
    )
    parser.add_argument("--dates", required=True, help="Comma-separated YYYY-MM-DD dates.")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--write", action="store_true", help="Persist audited run/publication markers.")
    args = parser.parse_args()

    dates = [value.strip() for value in args.dates.split(",") if value.strip()]
    if not dates:
        parser.error("--dates must contain at least one date")

    results: list[dict] = []
    with _connect(args.db, read_only=not args.write) as conn:
        if args.write:
            ensure_full_market_batch_schema(conn)
            conn.commit()
        for trade_date in dates:
            evaluation = evaluate_full_market_batch(conn, trade_date)
            if args.write:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    evaluation = record_full_market_batch(
                        conn,
                        evaluation,
                        storage_status="backfill_verified",
                        source_summary=[{
                            "source": "persisted_official_classification",
                            "mode": "explicit_marker_backfill",
                        }],
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            results.append(_report_view(evaluation))

    print(json.dumps({"write": args.write, "db": str(args.db), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
