from __future__ import annotations

"""Build overseas, TAIFEX night, and official valuation history in a DB candidate."""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.config import DB_PATH, default_portable_db_path  # noqa: E402
from core.db import assert_db_integrity, db  # noqa: E402
from services.database_hygiene_cleanup_service import (  # noqa: E402
    reconcile_official_credit_legacy_mirrors,
    reconcile_official_institution_legacy_mirror,
    remove_exact_duplicate_indexes,
)
from services.external_history_materializer import (  # noqa: E402
    materialize_global_history,
    materialize_official_valuation_history,
    materialize_taifex_night_history,
    verified_price_dates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--global-market-days", type=int, default=400)
    parser.add_argument("--night-market-days", type=int, default=400)
    parser.add_argument("--valuation-days", type=int, default=200)
    parser.add_argument("--calendar-days", type=int, default=760)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--lock-wait-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if DB_PATH.resolve() == default_portable_db_path().resolve():
        raise SystemExit("refusing direct active-DB write; run through isolated publisher")

    # The isolated publisher owns the full pre/post integrity gates.
    assert_db_integrity()
    valuation_dates = verified_price_dates(limit=max(int(args.valuation_days), 1))
    if len(valuation_dates) != max(int(args.valuation_days), 1):
        raise SystemExit("not enough verified local official-price dates for valuation history")
    calendar_days = max(int(args.calendar_days), 60)
    end_date = date.today()
    start_date = end_date - timedelta(days=calendar_days)

    print(json.dumps({"stage": "global_history", "status": "started"}), flush=True)
    global_result = materialize_global_history(
        calendar_days=calendar_days,
        retain_market_days=max(int(args.global_market_days), 1),
        max_workers=args.max_workers,
    )
    print(json.dumps({"stage": "global_history", **global_result}, ensure_ascii=False), flush=True)

    print(json.dumps({"stage": "taifex_night_history", "status": "started"}), flush=True)
    night_result = materialize_taifex_night_history(
        start_date=start_date,
        end_date=end_date,
        retain_days=max(int(args.night_market_days), 1),
        max_workers=min(max(int(args.max_workers), 1), 2),
    )
    print(json.dumps({"stage": "taifex_night_history", **night_result}, ensure_ascii=False), flush=True)

    print(json.dumps({"stage": "official_valuation_history", "status": "started"}), flush=True)
    valuation_result = materialize_official_valuation_history(
        trading_dates=valuation_dates,
        retain_days=max(int(args.valuation_days), 1),
        max_workers=args.max_workers,
    )
    print(
        json.dumps(
            {
                "stage": "official_valuation_history",
                **{key: value for key, value in valuation_result.items() if key != "per_date_source_counts"},
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    print(json.dumps({"stage": "database_hygiene_cleanup", "status": "started"}), flush=True)
    with db() as conn:
        conn.execute("BEGIN")
        institution_mirror_result = reconcile_official_institution_legacy_mirror(conn)
        credit_mirror_result = reconcile_official_credit_legacy_mirrors(conn)
        index_cleanup_result = remove_exact_duplicate_indexes(conn)
        hygiene_result = {
            "ok": bool(
                institution_mirror_result.get("ok")
                and credit_mirror_result.get("ok")
                and index_cleanup_result.get("ok")
            ),
            "institution_legacy_mirror": institution_mirror_result,
            "credit_legacy_mirrors": credit_mirror_result,
            "index_cleanup": index_cleanup_result,
            "business_rows_deleted": 0,
        }
        if hygiene_result.get("ok"):
            conn.commit()
        else:
            conn.rollback()
    print(
        json.dumps(
            {"stage": "database_hygiene_cleanup", **hygiene_result},
            ensure_ascii=False,
        ),
        flush=True,
    )

    assert_db_integrity()
    publishable = bool(
        global_result.get("ok")
        and night_result.get("ok")
        and valuation_result.get("ok")
        and hygiene_result.get("ok")
    )
    report = {
        "ok": publishable,
        "status": "ok" if publishable else "failed",
        "database": str(DB_PATH),
        "retention_contract": {
            "global_market_days": max(int(args.global_market_days), 1),
            "taifex_night_days": max(int(args.night_market_days), 1),
            "official_valuation_days": max(int(args.valuation_days), 1),
        },
        "point_in_time_note": (
            "historical imports retain actual retrieval time; canonical cutoff readers "
            "will not expose these rows before that time"
        ),
        "legacy_valuation_note": (
            "legacy valuation was not historically backfilled because it has no immutable "
            "available_at contract"
        ),
        "global_market": global_result,
        "taifex_night": night_result,
        "official_valuation": valuation_result,
        "database_hygiene_cleanup": hygiene_result,
    }
    report_path = args.report if args.report.is_absolute() else ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": publishable, "report": str(report_path)}, ensure_ascii=False, indent=2))
    return 0 if publishable else 4


if __name__ == "__main__":
    raise SystemExit(main())
