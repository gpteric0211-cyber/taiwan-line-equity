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
from services.external_event_service import refresh_external_market_events  # noqa: E402
from services.official_event_service import refresh_official_company_events  # noqa: E402
from services.taifex_night_snapshot_service import refresh_taifex_night_snapshot  # noqa: E402
from services.trading_restriction_service import refresh_official_trading_restrictions  # noqa: E402
from core.market_session import recent_market_date_for_post_close  # noqa: E402

try:  # Support both ``python scripts/x.py`` and ``import scripts.x``.
    from scripts.run_isolated_post_close_pipeline import (  # type: ignore
        DEFAULT_LOCK as MARKET_DATABASE_UPDATE_LOCK,
        isolated_update_lock,
    )
except ModuleNotFoundError:
    from run_isolated_post_close_pipeline import (  # type: ignore
        DEFAULT_LOCK as MARKET_DATABASE_UPDATE_LOCK,
        isolated_update_lock,
    )


def update_external_analysis_context(
    *,
    dry_run: bool = False,
    expected_date: str | None = None,
) -> dict[str, object]:
    effective_date = expected_date or recent_market_date_for_post_close()
    global_market = refresh_global_market_snapshot(dry_run=dry_run)
    taifex_night = refresh_taifex_night_snapshot(dry_run=dry_run)
    official_events = refresh_official_company_events(
        dry_run=dry_run,
        expected_date=effective_date,
    )
    trading_restrictions = refresh_official_trading_restrictions(dry_run=dry_run)
    external_events = refresh_external_market_events(dry_run=dry_run)
    ok = all(
        bool(item.get("ok"))
        for item in (
            global_market,
            taifex_night,
            official_events,
            trading_restrictions,
            external_events,
        )
    )
    return {
        "ok": ok,
        "status": "ok" if ok else "partial",
        "dry_run": dry_run,
        "expected_date": effective_date,
        "global_market": global_market,
        "taifex_night": taifex_night,
        "official_events": official_events,
        "trading_restrictions": trading_restrictions,
        "external_events": external_events,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh persisted global, TAIFEX, official filing, trading-restriction, "
            "revenue, policy, and licensed-event context."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--lock-wait-seconds",
        type=float,
        default=3300,
        help="Wait for another scheduled market database writer to finish.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "logs" / "market_foundation" / "external_analysis_context_latest.json",
    )
    args = parser.parse_args(argv)
    if args.dry_run:
        result = update_external_analysis_context(dry_run=True)
    else:
        with isolated_update_lock(
            MARKET_DATABASE_UPDATE_LOCK,
            wait_seconds=max(0.0, args.lock_wait_seconds),
        ) as acquired:
            result = (
                update_external_analysis_context(dry_run=False)
                if acquired
                else {
                    "ok": False,
                    "status": "market_database_update_lock_busy",
                    "lock_wait_seconds": max(0.0, args.lock_wait_seconds),
                }
            )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix(args.report.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 4


if __name__ == "__main__":
    from core.tls_config import configure_tls
    configure_tls()
    raise SystemExit(main())
