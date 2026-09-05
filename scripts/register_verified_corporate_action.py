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
from repository.corporate_action_repository import (  # noqa: E402
    CorporateActionRecord,
    register_verified_corporate_action,
)


def _integrity(conn: sqlite3.Connection, pragma: str) -> list[str]:
    return [str(row[0]) for row in conn.execute(f"PRAGMA {pragma}")]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or persist one official permanent corporate action."
    )
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--code", required=True)
    parser.add_argument("--company-name")
    parser.add_argument("--action-type", required=True)
    parser.add_argument("--effective-date", required=True)
    parser.add_argument("--adjustment-method", required=True)
    parser.add_argument("--stock-distribution-ratio", type=float)
    parser.add_argument("--ratio-unit")
    parser.add_argument("--cash-dividend-per-share", type=float)
    parser.add_argument("--share-count-factor", type=float)
    parser.add_argument("--pre-event-price-multiplier", type=float)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-record-key", required=True)
    parser.add_argument("--publisher-published-at")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist after full preflight; omission performs validation only.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    record = CorporateActionRecord(
        code=args.code,
        company_name=args.company_name,
        action_type=args.action_type,
        effective_date=args.effective_date,
        adjustment_method=args.adjustment_method,
        stock_distribution_ratio=args.stock_distribution_ratio,
        ratio_unit=args.ratio_unit,
        cash_dividend_per_share=args.cash_dividend_per_share,
        share_count_factor=args.share_count_factor,
        pre_event_price_multiplier=args.pre_event_price_multiplier,
        source_id=args.source_id,
        source_url=args.source_url,
        source_record_key=args.source_record_key,
        publisher_published_at=args.publisher_published_at,
    )
    if not args.write:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = register_verified_corporate_action(
                conn,
                record,
            )
            conn.rollback()
        print(json.dumps({**result, "status": "validated", "writes_db": False}))
        return 0

    target = args.db.resolve()
    if not target.is_file() or target.stat().st_size == 0:
        raise FileNotFoundError(f"database is missing or empty: {target.name}")
    conn = sqlite3.connect(target, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        before = _integrity(conn, "quick_check")
        if before != ["ok"]:
            raise RuntimeError(
                "database quick_check failed before write: " + "; ".join(before[:20])
            )
        conn.execute("BEGIN IMMEDIATE")
        result = register_verified_corporate_action(
            conn,
            record,
        )
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError("foreign_key_check failed")
        after = _integrity(conn, "quick_check")
        if after != ["ok"]:
            raise RuntimeError(
                "database quick_check failed after write: " + "; ".join(after[:20])
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(
        json.dumps(
            {
                **result,
                "writes_db": bool(
                    result["canonical_changed"] or result["legacy_changed"]
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
