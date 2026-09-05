from __future__ import annotations

from typing import Any

from analysis.estimated_chip_cost import (
    build_canonical_cost_snapshot as build_analysis_cost_snapshot,
)
from core.cost_source_registry import CANONICAL_COST_FORMULA_VERSION
from services.bot_market_data_service import _institutional_context_payload
from services.estimated_chip_cost_service import build_canonical_cost_snapshot
from services.line_bot_service import _compact_daily


TRADE_DATE = "2026-08-27"
CODE = "2454"
FORMULA_V3 = "official_net_flow_incremental_inventory_v3"
PARITY_FIELDS = (
    "value",
    "trade_date",
    "status",
    "calculation_state",
    "confidence",
    "sample_days",
    "available",
    "contract_version",
    "formula_version",
)


def _cost_row(
    cost_type: str,
    estimated_cost: float,
    *,
    confidence: str = "medium",
    sample_days: int = 60,
) -> dict[str, Any]:
    return {
        "code": CODE,
        "trade_date": TRADE_DATE,
        "cost_type": cost_type,
        "estimated_cost": estimated_cost,
        "cost_status": "estimated",
        "confidence": confidence,
        "data_source_confidence": "high",
        "data_source_status": f"official_only;continuous_sessions={sample_days}",
        "formula_version": FORMULA_V3,
        "sample_days": sample_days,
        "estimate_start_date": "2026-06-05",
        "estimate_end_date": TRADE_DATE,
    }


def _project_all_surfaces(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    analysis_snapshot = build_analysis_cost_snapshot(rows, TRADE_DATE)
    service_snapshot = build_canonical_cost_snapshot(
        rows,
        expected_date=TRADE_DATE,
    )
    bot_payload = _institutional_context_payload(
        {
            "institution_rows": [],
            "estimated_cost_rows": rows,
        },
        TRADE_DATE,
    )
    return analysis_snapshot, service_snapshot, bot_payload


def _parity_projection(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item.get(field) for field in PARITY_FIELDS}


def test_medium_60_session_v3_costs_match_analysis_service_and_bot() -> None:
    assert CANONICAL_COST_FORMULA_VERSION == FORMULA_V3
    rows = [
        _cost_row("foreign_estimated", 2432.64),
        _cost_row("trust_estimated", 1865.34),
    ]

    analysis_snapshot, service_snapshot, bot_payload = _project_all_surfaces(rows)

    assert analysis_snapshot == service_snapshot
    assert service_snapshot["formula_version"] == FORMULA_V3
    assert service_snapshot["trade_date"] == TRADE_DATE
    assert bot_payload["cost_contract_version"] == service_snapshot["contract_version"]
    assert service_snapshot["can_override_main_status"] is False
    assert bot_payload["can_override_main_status"] is False

    for cost_type in ("foreign_estimated", "trust_estimated"):
        service_item = service_snapshot["costs"][cost_type]
        bot_item = bot_payload["canonical_costs"][cost_type]

        assert _parity_projection(bot_item) == _parity_projection(service_item)
        assert service_item["available"] is True
        assert service_item["confidence"] == "medium"
        assert service_item["formula_version"] == FORMULA_V3
        assert service_item["trade_date"] == TRADE_DATE
        assert service_item["can_override_main_status"] is False
        assert bot_item["can_override_main_status"] is False


def test_low_confidence_59_session_costs_are_hidden_on_both_surfaces() -> None:
    rows = [
        _cost_row(
            "foreign_estimated",
            2432.64,
            confidence="low",
            sample_days=59,
        ),
        _cost_row(
            "trust_estimated",
            1865.34,
            confidence="low",
            sample_days=59,
        ),
    ]

    analysis_snapshot, service_snapshot, bot_payload = _project_all_surfaces(rows)

    assert analysis_snapshot == service_snapshot
    assert service_snapshot["estimated_costs"] == []
    assert bot_payload["estimated_costs"] == []
    assert bot_payload["cost_contract_version"] == service_snapshot["contract_version"]
    assert service_snapshot["can_override_main_status"] is False
    assert bot_payload["can_override_main_status"] is False

    for cost_type in ("foreign_estimated", "trust_estimated"):
        service_item = service_snapshot["costs"][cost_type]
        bot_item = bot_payload["canonical_costs"][cost_type]

        assert _parity_projection(bot_item) == _parity_projection(service_item)
        assert service_item["value"] is None
        assert service_item["estimated_cost"] is None
        assert service_item["available"] is False
        assert service_item["confidence"] == "low"
        assert service_item["formula_version"] == FORMULA_V3
        assert service_item["trade_date"] == TRADE_DATE
        assert service_item["can_override_main_status"] is False
        assert bot_item["value"] is None
        assert bot_item["estimated_cost"] is None
        assert bot_item["can_override_main_status"] is False


def test_line_compaction_preserves_full_canonical_cost_contract() -> None:
    rows = [
        _cost_row(
            "foreign_estimated",
            2432.64,
            confidence="low",
            sample_days=59,
        ),
        _cost_row("trust_estimated", 1865.34),
    ]
    _analysis_snapshot, service_snapshot, bot_context = _project_all_surfaces(rows)
    facts = _compact_daily(
        {
            "status": "ok",
            "code": CODE,
            "trade_date": TRADE_DATE,
            "freshness": {"status": "current", "ready": True},
            "ohlcv": {
                "date": TRADE_DATE,
                "close": 2000.0,
                "official_trusted": True,
            },
            "technical": {
                "available": False,
                "status": "unavailable",
                "decision_ready": False,
            },
            "valuation": {"available": False, "status": "unavailable"},
            "data_quality": {"decision_ready": False},
            "institutional_context": bot_context,
        }
    )

    compact = facts["institutional_context"]
    assert compact["cost_contract_version"] == service_snapshot["contract_version"]
    assert compact["canonical_costs"] == service_snapshot["costs"]
    assert compact["canonical_costs"]["foreign_estimated"]["value"] is None
    assert compact["canonical_costs"]["foreign_estimated"]["status"] == "insufficient_history"
    assert compact["canonical_costs"]["trust_estimated"]["value"] == 1865.34
