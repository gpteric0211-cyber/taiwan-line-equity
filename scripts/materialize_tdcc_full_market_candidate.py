from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH, default_portable_db_path  # noqa: E402
from core.db import assert_db_integrity, db  # noqa: E402
from repository.market_analytics_repository import active_stock_codes  # noqa: E402
from services.tdcc_equity_concentration_service import (  # noqa: E402
    rebuild_tdcc_equity_summaries_from_persisted_distribution,
    update_tdcc_equity_concentration_for_codes,
)
from task.v11_structured_fact_materializer import (  # noqa: E402
    materialize_tdcc_candidate_revisions,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize full-market TDCC data inside an isolated candidate DB."
    )
    parser.add_argument("--allow-candidate-write", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.allow_candidate_write:
        raise SystemExit("explicit --allow-candidate-write is required")
    if DB_PATH.resolve() == default_portable_db_path().resolve():
        raise SystemExit(
            "refusing direct active-DB write; run through run_isolated_update"
        )
    assert_db_integrity(full=True)
    with db() as conn:
        codes = active_stock_codes(conn)
    update = update_tdcc_equity_concentration_for_codes(
        codes,
        source="tdcc",
        dry_run=False,
    )
    if not update.get("ok"):
        raise RuntimeError(
            f"TDCC full-market source did not pass: {update.get('errors') or update}"
        )
    with db() as conn:
        rebuild = rebuild_tdcc_equity_summaries_from_persisted_distribution(conn)
        revisions = materialize_tdcc_candidate_revisions(conn, stock_codes=codes)
        conn.commit()
    assert_db_integrity(full=True)
    payload = {
        "ok": True,
        "database_role": "isolated_candidate",
        "source": update.get("source"),
        "latest_date": update.get("latest_date"),
        "requested_code_count": len(codes),
        "success_count": update.get("success"),
        "missing_count": update.get("missing"),
        "distribution_rows_upserted": update.get("distribution_rows"),
        "summaries_rebuilt": rebuild,
        "candidate_revisions": revisions,
    }
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
