from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from scripts.update_all_market_database import (  # noqa: E402
    evaluate_update_readiness,
    official_update_exit_code,
)


def test_official_core_can_publish_while_supplemental_price_volume_is_pending() -> None:
    readiness = evaluate_update_readiness(
        ({"ok": True}, {"ok": True}, {"ok": True}),
        {"ok": False, "operational_complete": False},
    )

    assert readiness == {
        "official_core_ready": True,
        "price_volume_capture_ready": False,
        "price_volume_scoring_ready": False,
        "price_volume_ready": False,
        "price_volume_operational_complete": False,
        "full_analysis_ready": False,
        "status": "official_complete_supplemental_pending",
    }


def test_required_official_component_failure_still_blocks_publication() -> None:
    readiness = evaluate_update_readiness(
        ({"ok": True}, {"ok": False}),
        {"ok": True, "operational_complete": True},
    )

    assert readiness["official_core_ready"] is False
    assert readiness["full_analysis_ready"] is False
    assert readiness["status"] == "partial"


def test_attempted_but_rejected_price_volume_is_not_decision_ready() -> None:
    readiness = evaluate_update_readiness(
        ({"ok": True},),
        {"ok": False, "operational_complete": True, "rejected_count": 260},
    )

    assert readiness["price_volume_operational_complete"] is True
    assert readiness["price_volume_ready"] is False
    assert readiness["full_analysis_ready"] is False
    assert readiness["status"] == "official_complete_supplemental_pending"


def test_complete_daily_capture_does_not_fake_missing_historical_score_coverage() -> None:
    readiness = evaluate_update_readiness(
        ({"ok": True},),
        {
            "ok": True,
            "operational_complete": True,
            "required_trading_stock_count": 1938,
            "decision_ready_count": 0,
        },
    )

    assert readiness["price_volume_capture_ready"] is True
    assert readiness["price_volume_scoring_ready"] is False
    assert readiness["price_volume_ready"] is False
    assert readiness["full_analysis_ready"] is False
    assert readiness["status"] == "official_complete_analysis_history_pending"


def test_official_update_uses_fixed_source_delay_partial_and_fatal_exit_codes() -> None:
    assert official_update_exit_code({"ok": True}) == 0
    assert official_update_exit_code(
        {
            "ok": False,
            "twse_valuation": {"ok": False, "status": "source_delayed"},
        }
    ) == 5
    assert official_update_exit_code(
        {
            "ok": False,
            "twse_valuation": {"ok": False, "status": "source_delayed"},
            "official_exact_date_ohlcv": {"ok": False, "status": "partial"},
        }
    ) == 4
    assert official_update_exit_code(
        {
            "ok": False,
            "twse_valuation": {"ok": False, "status": "failed"},
        }
    ) == 2
