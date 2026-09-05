from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.tpex import fetch_tpex_peratio_analysis  # noqa: E402
from core.db import db  # noqa: E402
from core.market_session import (  # noqa: E402
    recent_market_date_for_eod,
    recent_market_date_for_post_close,
)
from repository.market_analytics_repository import active_stock_codes  # noqa: E402
from services.market_analytics_service import rebuild_daily_technical_snapshots  # noqa: E402
from services.market_foundation_importer import run_market_foundation_update  # noqa: E402
from services.full_market_history_service import refresh_full_market_history_date  # noqa: E402
from services.global_market_snapshot_service import refresh_global_market_snapshot  # noqa: E402
from services.estimated_chip_cost_service import refresh_estimated_chip_costs  # noqa: E402
from services.institution_snapshot_service import refresh_official_institution_snapshot  # noqa: E402
from services.official_event_service import refresh_official_company_events  # noqa: E402
from services.official_credit_balance_service import refresh_official_credit_balances  # noqa: E402
from services.price_volume_daily_service import (  # noqa: E402
    official_result_date,
    recover_missing_price_volume_from_persisted_trades,
    reconcile_full_market_price_volume_after_official_update,
)
from services.stock_master_service import sync_official_stock_master  # noqa: E402
from services.stock_entity_registry_materializer import materialize_stock_entity_registry  # noqa: E402
from services.tpex_valuation_service import refresh_tpex_valuation_codes  # noqa: E402
from services.twse_valuation_service import update_twse_daily_valuation  # noqa: E402
from services.taifex_night_snapshot_service import refresh_taifex_night_snapshot  # noqa: E402
from services.technical_ensemble_materializer import materialize_technical_ensemble_v1  # noqa: E402
from services.trading_restriction_service import refresh_official_trading_restrictions  # noqa: E402


def evaluate_update_readiness(
    required_components: tuple[dict, ...],
    price_volume: dict,
) -> dict[str, bool | str]:
    """Keep official publication readiness independent from supplemental data."""

    official_core_ready = all(bool(item.get("ok")) for item in required_components)
    # Operational completion only means every required stock was attempted.
    # Capture readiness means every daily profile is complete for its declared
    # scope. Scoring readiness is stricter: every required stock also needs the
    # frozen historical coverage gate to produce a decision-usable score.
    price_volume_operational_complete = bool(price_volume.get("operational_complete"))
    price_volume_capture_ready = bool(price_volume.get("ok"))
    required_count = int(price_volume.get("required_trading_stock_count") or 0)
    decision_ready_count = int(price_volume.get("decision_ready_count") or 0)
    price_volume_scoring_ready = bool(
        price_volume_capture_ready
        and required_count > 0
        and decision_ready_count >= required_count
    )
    # Backward-compatible name: ``price_volume_ready`` has always meant usable
    # by the decision layer, not merely present in storage.
    price_volume_ready = price_volume_scoring_ready
    full_analysis_ready = bool(official_core_ready and price_volume_scoring_ready)
    status = (
        "ok"
        if full_analysis_ready
        else (
            "official_complete_analysis_history_pending"
            if official_core_ready and price_volume_capture_ready
            else
            "official_complete_supplemental_pending"
            if official_core_ready
            else "partial"
        )
    )
    return {
        "official_core_ready": official_core_ready,
        "price_volume_capture_ready": price_volume_capture_ready,
        "price_volume_scoring_ready": price_volume_scoring_ready,
        "price_volume_ready": price_volume_ready,
        "price_volume_operational_complete": price_volume_operational_complete,
        "full_analysis_ready": full_analysis_ready,
        "status": status,
    }


def official_update_exit_code(result: dict) -> int:
    """Map the official update result to the fixed scheduled-job contract."""

    if result.get("ok"):
        return 0
    component_keys = (
        "stock_master",
        "official_exact_date_ohlcv",
        "twse_valuation",
        "tpex_valuation",
        "technical_snapshots",
        "official_institution_activity",
        "official_credit_balances",
        "estimated_institution_cost",
        "official_trading_restrictions",
    )
    failures = [
        result.get(key) or {}
        for key in component_keys
        if key in result and not bool((result.get(key) or {}).get("ok"))
    ]
    statuses = {str(item.get("status") or "").strip().lower() for item in failures}
    if statuses & {"failed", "fatal", "error", "unavailable"}:
        return 2
    if statuses & {"partial", "invalid", "quality_failed"}:
        return 4
    if failures and statuses <= {"source_delayed"}:
        return 5
    return 4


def run_all_market_update(
    *,
    run_date: str | None = None,
    dry_run: bool = False,
) -> dict:
    target_date = run_date or recent_market_date_for_eod()
    master = sync_official_stock_master(dry_run=dry_run)
    if dry_run:
        entity_registry = materialize_stock_entity_registry(None, dry_run=True)
    else:
        with closing(db()) as conn:
            entity_registry = materialize_stock_entity_registry(conn)
            conn.commit()
    foundation = run_market_foundation_update(
        run_date=target_date,
        official_only=True,
        include_scraped=False,
        allow_full_scrape=False,
        dry_run=dry_run,
    )
    latest_post_close_date = recent_market_date_for_post_close()
    trading_restrictions = (
        refresh_official_trading_restrictions(
            dry_run=dry_run,
            as_of_date=target_date,
        )
        if target_date == latest_post_close_date
        else {
            "ok": True,
            "status": "skipped_historical_run",
            "data_date": target_date,
            "reason": "current-list official endpoints are not backdated to avoid look-ahead bias",
        }
    )
    exact_history = refresh_full_market_history_date(target_date, dry_run=dry_run)
    effective_date = (
        target_date
        if exact_history.get("storage_allowed")
        and exact_history.get("trade_date") == target_date
        else official_result_date(foundation)
    )
    twse_valuation = update_twse_daily_valuation(effective_date, dry_run=dry_run)
    if dry_run:
        tpex_rows = fetch_tpex_peratio_analysis()
        tpex_valuation = {
            "ok": bool(tpex_rows),
            "dry_run": True,
            "rows_read": len(tpex_rows),
            "rows_written": 0,
        }
    else:
        with closing(db()) as conn:
            otc_codes = active_stock_codes(conn, market="otc")
        tpex_valuation = refresh_tpex_valuation_codes(otc_codes)
    technical = rebuild_daily_technical_snapshots(
        trade_date=effective_date,
        backfill=False,
        dry_run=dry_run,
    )
    try:
        technical_ensemble = materialize_technical_ensemble_v1(
            trade_date=effective_date,
            backfill=False,
            dry_run=dry_run,
        )
    except Exception as exc:
        # Candidate persistence is observable but cannot make the protected
        # official update unavailable before the V3 release gate.
        technical_ensemble = {
            "ok": False,
            "status": "candidate_materializer_failed",
            "error_class": type(exc).__name__,
            "required_for_official_update": False,
        }
    institution = refresh_official_institution_snapshot(
        trade_date=effective_date,
        dry_run=dry_run,
    )
    credit_balances = refresh_official_credit_balances(
        effective_date,
        dry_run=dry_run,
    )
    estimated_cost = refresh_estimated_chip_costs(
        as_of_date=effective_date,
        dry_run=dry_run,
    )
    price_volume_recovery = (
        {"ok": True, "status": "dry_run", "target_date": effective_date, "recovered_count": 0}
        if dry_run
        else recover_missing_price_volume_from_persisted_trades(effective_date)
    )
    price_volume = (
        {"ok": True, "status": "dry_run", "target_date": effective_date}
        if dry_run
        else reconcile_full_market_price_volume_after_official_update({
            "verified_trade_date": effective_date,
            "official_sources": [],
        })
    )
    global_market = refresh_global_market_snapshot(dry_run=dry_run)
    taifex_night = refresh_taifex_night_snapshot(dry_run=dry_run)
    official_events = refresh_official_company_events(
        dry_run=dry_run,
        expected_date=effective_date,
    )
    readiness = evaluate_update_readiness(
        (
            master,
            exact_history,
            twse_valuation,
            tpex_valuation,
            technical,
            institution,
            credit_balances,
            estimated_cost,
            trading_restrictions,
        ),
        price_volume,
    )
    return {
        # ``ok`` deliberately means the official/required core is publishable.
        # Supplemental Fugle price-volume readiness is reported separately and
        # remains a full-pipeline retry condition in run_post_close_daily_pipeline.
        "ok": readiness["official_core_ready"],
        "status": readiness["status"],
        "official_core_ready": readiness["official_core_ready"],
        "price_volume_capture_ready": readiness["price_volume_capture_ready"],
        "price_volume_scoring_ready": readiness["price_volume_scoring_ready"],
        "price_volume_ready": readiness["price_volume_ready"],
        "price_volume_operational_complete": readiness["price_volume_operational_complete"],
        "full_analysis_ready": readiness["full_analysis_ready"],
        "dry_run": dry_run,
        "run_date": run_date,
        "effective_trade_date": effective_date,
        "stock_master": master,
        "stock_entity_registry": entity_registry,
        "official_ohlcv": foundation,
        "official_exact_date_ohlcv": exact_history,
        "twse_valuation": twse_valuation,
        "tpex_valuation": tpex_valuation,
        "technical_snapshots": technical,
        "technical_ensemble_candidate": technical_ensemble,
        "official_institution_activity": institution,
        "official_credit_balances": credit_balances,
        "estimated_institution_cost": estimated_cost,
        "price_volume_recovery": price_volume_recovery,
        "price_volume_reconciliation": price_volume,
        "global_market_snapshot": global_market,
        "taifex_night_snapshot": taifex_night,
        "official_company_events": official_events,
        "official_trading_restrictions": trading_restrictions,
        "global_market_required_for_official_update": False,
        "supplemental_analysis_ready": all(
            bool(item.get("ok"))
            for item in (institution, estimated_cost, global_market, taifex_night, official_events)
        ),
        "price_volume_note": (
            "Missing regular-session price bins may be losslessly rebuilt from persisted licensed "
            "Fugle trades; no volume, side, odd-lot, or after-hours value is estimated."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update official all-listed/all-OTC daily data and derived technical snapshots."
    )
    parser.add_argument("--date", dest="run_date", help="Optional target trade date YYYY-MM-DD.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "docs" / "ALL_MARKET_DATABASE_UPDATE_REPORT.json",
    )
    args = parser.parse_args()
    result = run_all_market_update(run_date=args.run_date, dry_run=args.dry_run)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return official_update_exit_code(result)


if __name__ == "__main__":
    from core.tls_config import configure_tls
    configure_tls()
    raise SystemExit(main())
