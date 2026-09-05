from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.tpex import TPEX_PERATIO_ANALYSIS_URL, fetch_tpex_peratio_analysis  # noqa: E402
from core.date_utils import normalize_date  # noqa: E402
from core.db import init_db  # noqa: E402
from repository.twse_valuation_repository import (  # noqa: E402
    cleanup_twse_daily_valuation,
    upsert_twse_daily_valuations,
)


SAMPLE_CODES = ["3491", "6806", "2451"]


def _contains(rows: list[dict[str, Any]], code: str) -> bool:
    return any(str(row.get("symbol") or "") == code for row in rows)


def _sample_rows(rows: list[dict[str, Any]], codes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for code in codes:
        match = next((row for row in rows if str(row.get("symbol") or "") == code), None)
        if match:
            out[code] = {
                "data_date": match.get("data_date"),
                "name": match.get("name"),
                "pe_ratio": match.get("pe_ratio"),
                "pb_ratio": match.get("pb_ratio"),
                "dividend_yield": match.get("dividend_yield"),
                "source": match.get("source"),
            }
        else:
            out[code] = None
    return out


def run_update(
    *,
    requested_date: str | None,
    latest: bool,
    retention_days: int,
    dry_run: bool,
) -> dict[str, Any]:
    rows = fetch_tpex_peratio_analysis()
    if not rows:
        return {
            "ok": False,
            "source": "TPEX_PERATIO_ANALYSIS",
            "url": TPEX_PERATIO_ANALYSIS_URL,
            "requested_mode": "latest" if latest else "date",
            "requested_date": requested_date,
            "row_count": 0,
            "writes_db": False,
            "error_reason": "TPEx official valuation endpoint returned no parsed rows.",
        }
    data_dates = sorted({str(row.get("data_date")) for row in rows if row.get("data_date")})
    endpoint_date = data_dates[-1] if data_dates else None
    normalized_requested = normalize_date(requested_date)
    requested_mode = "latest" if latest or not normalized_requested else "date"
    resolved_trade_date = endpoint_date if requested_mode == "latest" else normalized_requested
    date_available = bool(endpoint_date and resolved_trade_date == endpoint_date)
    filtered = [row for row in rows if row.get("data_date") == resolved_trade_date] if date_available else []

    if not date_available:
        return {
            "ok": False,
            "source": "TPEX_PERATIO_ANALYSIS",
            "url": TPEX_PERATIO_ANALYSIS_URL,
            "requested_mode": requested_mode,
            "requested_date": normalized_requested,
            "resolved_trade_date": resolved_trade_date,
            "endpoint_date": endpoint_date,
            "data_dates": data_dates,
            "row_count": len(rows),
            "write_count": 0,
            "writes_db": False,
            "contains": {code: _contains(rows, code) for code in SAMPLE_CODES},
            "sample": _sample_rows(rows, SAMPLE_CODES),
            "error_reason": "Requested TPEx official valuation date is not available from the latest endpoint; rerun with --latest.",
        }

    if dry_run:
        write_count = 0
        cleanup = {"dry_run": True}
    else:
        init_db()
        write_count = upsert_twse_daily_valuations(filtered)
        cleanup = cleanup_twse_daily_valuation(retention_days=retention_days)
    return {
        "ok": True,
        "source": "TPEX_PERATIO_ANALYSIS",
        "url": TPEX_PERATIO_ANALYSIS_URL,
        "requested_mode": requested_mode,
        "requested_date": normalized_requested,
        "resolved_trade_date": resolved_trade_date,
        "endpoint_date": endpoint_date,
        "data_date": resolved_trade_date,
        "row_count": len(filtered),
        "write_count": write_count,
        "upsert_count": write_count,
        "dry_run": dry_run,
        "writes_db": bool(not dry_run),
        "retention_days": retention_days,
        "cleanup": cleanup,
        "contains": {code: _contains(filtered, code) for code in SAMPLE_CODES},
        "sample": _sample_rows(filtered, SAMPLE_CODES),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Update official TPEx daily valuation data.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", dest="requested_date", help="Requested trading date in YYYY-MM-DD format.")
    group.add_argument("--latest", action="store_true", help="Use the latest official TPEx available trading day.")
    parser.add_argument("--retention-days", type=int, default=200)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run_update(
        requested_date=args.requested_date,
        latest=bool(args.latest),
        retention_days=args.retention_days,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
