from __future__ import annotations

import sqlite3

import pytest

from analysis.estimated_chip_cost import DailyCostInput, calculate_institution_estimated_cost
from repository import estimated_chip_cost_repository as cost_repository
from services import estimated_chip_cost_service as cost_service


CANONICAL_FORMULA_VERSION = "official_net_flow_incremental_inventory_v3"
TRADE_DATE = "2026-08-27"
CODE = "2454"


def _cost_row(
    cost_type: str,
    estimated_cost: float,
    *,
    trade_date: str = TRADE_DATE,
    formula_version: str = CANONICAL_FORMULA_VERSION,
    code: str = CODE,
) -> dict[str, object]:
    return {
        "code": code,
        "trade_date": trade_date,
        "cost_type": cost_type,
        "cost_label": (
            "外資近期增量成本推估"
            if cost_type == "foreign_estimated"
            else "投信近期增量成本推估"
        ),
        "estimated_cost": estimated_cost,
        "cost_status": "estimated",
        "confidence": "medium",
        "data_source_confidence": "medium",
        "data_source_status": "official_only;continuous_sessions=60",
        "source_license": "public_local_db",
        "source_detail": "official institution flow and official OHLCV",
        "source_tables": "institution_activity_daily,history_price",
        "calculation_method": "official_net_flow_incremental_inventory",
        "formula_version": formula_version,
        "price_basis": "vwap_from_amount_volume",
        "price_basis_value": estimated_cost + 1,
        "price_to_cost_deviation_pct": 1.0,
        "accumulation_status": "neutral",
        "position_shares": 1000.0,
        "total_cost_amount": estimated_cost * 1000,
        "cumulative_net_shares": 1000.0,
        "estimate_start_date": "2026-07-01",
        "estimate_end_date": trade_date,
        "sample_days": 60,
        "display_reason": "官方法人淨流量近期增量成本推估。",
        "debug_reason": "official_net_flow_incremental_inventory",
        "missing_required_fields": None,
    }


def test_inventory_resets_after_full_liquidation_and_new_buy_starts_new_segment() -> None:
    rows = [
        DailyCostInput(CODE, "2026-08-25", 100.0, 1_000.0, 100_000.0, foreign_net=10.0, institution_source_quality="official"),
        DailyCostInput(CODE, "2026-08-26", 110.0, 1_000.0, 110_000.0, foreign_net=-15.0, institution_source_quality="official"),
        DailyCostInput(CODE, TRADE_DATE, 120.0, 1_000.0, 120_000.0, foreign_net=4.0, institution_source_quality="official"),
    ]

    calculated = calculate_institution_estimated_cost(rows, "foreign_estimated")

    liquidated = calculated[1]
    assert liquidated["position_shares"] == 0.0
    assert liquidated["total_cost_amount"] == 0.0
    assert liquidated["cumulative_net_shares"] == 0.0
    assert liquidated["estimate_start_date"] is None
    assert liquidated["sample_days"] == 0

    restarted = calculated[2]
    assert restarted["estimated_cost"] == pytest.approx(120.0)
    assert restarted["position_shares"] == 4.0
    assert restarted["total_cost_amount"] == pytest.approx(480.0)
    assert restarted["cumulative_net_shares"] == 4.0
    assert restarted["estimate_start_date"] == TRADE_DATE
    assert restarted["sample_days"] == 1


def test_canonical_reader_requires_exact_date_and_canonical_formula() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        cost_repository.ensure_estimated_chip_cost_schema(conn)
        cost_repository.upsert_estimated_chip_cost_rows(
            conn,
            [
                _cost_row(
                    "foreign_estimated",
                    88.0,
                    trade_date="2026-08-26",
                ),
                _cost_row(
                    "foreign_estimated",
                    91.0,
                    formula_version="official_net_flow_incremental_inventory_v2",
                ),
                _cost_row("trust_estimated", 102.5),
                _cost_row("foreign_estimated", 999.0, code="2330"),
            ],
        )
        conn.commit()

        rows = cost_repository.read_canonical_estimated_cost_rows(
            conn,
            code=CODE,
            trade_date=TRADE_DATE,
        )
    finally:
        conn.close()

    assert [row["cost_type"] for row in rows] == ["trust_estimated"]
    assert rows[0]["trade_date"] == TRADE_DATE
    assert rows[0]["formula_version"] == CANONICAL_FORMULA_VERSION
    assert rows[0]["estimated_cost"] == pytest.approx(102.5)


def test_canonical_snapshot_has_same_contract_for_foreign_and_trust() -> None:
    rows = [
        _cost_row("trust_estimated", 102.5),
        _cost_row("foreign_estimated", 101.25),
    ]

    snapshot = cost_service.build_canonical_cost_snapshot(
        rows,
        expected_date=TRADE_DATE,
    )
    reversed_snapshot = cost_service.build_canonical_cost_snapshot(
        list(reversed(rows)),
        expected_date=TRADE_DATE,
    )

    assert snapshot == reversed_snapshot
    assert snapshot["trade_date"] == TRADE_DATE
    assert snapshot["formula_version"] == CANONICAL_FORMULA_VERSION
    assert snapshot["can_override_main_status"] is False

    costs = snapshot["costs"]
    foreign = costs["foreign_estimated"]
    trust = costs["trust_estimated"]
    required_contract = {
        "cost_type",
        "trade_date",
        "value",
        "available",
        "status",
        "confidence",
        "is_estimated",
        "can_override_main_status",
    }
    assert required_contract <= set(foreign)
    assert set(foreign) == set(trust)
    assert foreign["value"] == pytest.approx(101.25)
    assert trust["value"] == pytest.approx(102.5)
    assert foreign["status"] == trust["status"] == "estimated"
    assert foreign["available"] is trust["available"] is True
    assert foreign["is_estimated"] is trust["is_estimated"] is True
    assert foreign["can_override_main_status"] is False
    assert trust["can_override_main_status"] is False
