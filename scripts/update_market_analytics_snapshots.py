from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.market_analytics_service import rebuild_daily_technical_snapshots  # noqa: E402
from services.stock_master_service import sync_official_stock_master  # noqa: E402
from services.technical_ensemble_materializer import materialize_technical_ensemble_v1  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize the official Taiwan stock master and persist daily technical snapshots."
    )
    parser.add_argument("--date", help="Exact OHLCV trade date to calculate (YYYY-MM-DD).")
    parser.add_argument("--codes", help="Optional comma/space separated four-digit stock codes.")
    parser.add_argument("--backfill", action="store_true", help="Materialize every locally available history date.")
    parser.add_argument(
        "--legacy-snapshot-backfill",
        action="store_true",
        help=(
            "Also expand the legacy daily_technical_snapshot table historically. "
            "By default historical indicators use the complete compact vector table."
        ),
    )
    parser.add_argument("--skip-stock-master", action="store_true")
    parser.add_argument("--retention-days", type=int, default=600)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "docs" / "MARKET_ANALYTICS_UPDATE_REPORT.json",
    )
    args = parser.parse_args()

    codes = None
    if args.codes:
        codes = [item for item in args.codes.replace(",", " ").split() if item]
    master = (
        {"ok": True, "status": "skipped"}
        if args.skip_stock_master
        else sync_official_stock_master(dry_run=args.dry_run)
    )
    technical = rebuild_daily_technical_snapshots(
        codes=codes,
        trade_date=args.date,
        backfill=bool(args.backfill and args.legacy_snapshot_backfill),
        retain_trading_days=max(args.retention_days, 1),
        dry_run=args.dry_run,
    )
    technical_ensemble = materialize_technical_ensemble_v1(
        codes=codes,
        trade_date=args.date,
        backfill=args.backfill,
        retain_trading_days=max(args.retention_days, 600),
        dry_run=args.dry_run,
    )
    result = {
        "ok": bool(
            master.get("ok")
            and technical.get("ok")
            and technical_ensemble.get("ok")
        ),
        "stock_master": master,
        "technical_snapshots": technical,
        "technical_ensemble_candidate": technical_ensemble,
        "historical_technical_authority": "technical_indicator_vector_daily",
        "legacy_snapshot_scope": (
            "historical_backfill"
            if args.backfill and args.legacy_snapshot_backfill
            else "latest_compatibility_snapshot"
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
