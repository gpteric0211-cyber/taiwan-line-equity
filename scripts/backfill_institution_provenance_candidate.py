from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH, default_portable_db_path  # noqa: E402
from core.db import assert_db_integrity, db  # noqa: E402
from core.provenance_schema import (  # noqa: E402
    ensure_provenance_schema,
    record_validated_institution_activity_version,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill PIT versions for existing institution rows in a candidate DB."
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
    observed = datetime.now(timezone.utc)
    inserted = 0
    scanned = 0
    with db() as conn:
        ensure_provenance_schema(conn)
        cursor = conn.execute(
            """
            SELECT trade_date AS date,code,market,
                   foreign_buy,foreign_sell,foreign_net,
                   trust_buy,trust_sell,trust_net,
                   dealer_buy,dealer_sell,dealer_net,source
            FROM institution_activity_daily
            ORDER BY trade_date,code
            """
        )
        while True:
            batch = cursor.fetchmany(2000)
            if not batch:
                break
            for source in batch:
                scanned += 1
                inserted += int(
                    record_validated_institution_activity_version(
                        conn,
                        dict(source),
                        observed_at=observed,
                        ensure_schema=False,
                    )
                )
            conn.commit()
    assert_db_integrity(full=True)
    payload = {
        "ok": True,
        "database_role": "isolated_candidate",
        "scanned_rows": scanned,
        "new_point_in_time_versions": inserted,
        "observed_at": observed.isoformat(timespec="seconds"),
        "historical_visibility_rule": "not usable before observed_at plus 300 seconds",
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
