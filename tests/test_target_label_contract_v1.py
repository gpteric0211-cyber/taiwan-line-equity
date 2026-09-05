from __future__ import annotations

import math
import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.target_label_contract_v1 import (  # noqa: E402
    build_outcome_rows,
    derive_target_labels,
    target_sample_id,
)


def _labels(next_open: float, next_close: float):
    return derive_target_labels(
        t_close=100,
        next_open=next_open,
        next_close=next_close,
        adjustment_basis="official_adjusted",
    )


def test_exact_open_and_close_thresholds_are_inclusive() -> None:
    up = _labels(101, 101)
    down = _labels(99, 99)

    assert up["targets"]["next_open_gap"]["label"] == "up"
    assert up["targets"]["next_close_direction"]["label"] == "up"
    assert down["targets"]["next_open_gap"]["label"] == "down"
    assert down["targets"]["next_close_direction"]["label"] == "down"


def test_continuation_and_reversal_follow_gap_direction() -> None:
    up_continuation = _labels(102, 102.51)
    up_reversal = _labels(102, 101.49)
    down_continuation = _labels(98, 97.51)
    down_reversal = _labels(98, 98.49)

    assert up_continuation["targets"]["continuation_reversal"]["label"] == "continuation"
    assert up_reversal["targets"]["continuation_reversal"]["label"] == "reversal"
    assert down_continuation["targets"]["continuation_reversal"]["label"] == "continuation"
    assert down_reversal["targets"]["continuation_reversal"]["label"] == "reversal"


def test_continuation_target_is_unavailable_below_gap_eligibility() -> None:
    result = _labels(100.99, 103)
    target = result["targets"]["continuation_reversal"]

    assert target["label"] is None
    assert target["eligible"] is False
    assert target["quality_status"] == "unavailable"
    assert target["availability_reason"] == "absolute_open_gap_below_one_percent"


def test_bad_quality_suspension_nonfinite_and_wrong_basis_are_unavailable() -> None:
    cases = [
        {"t_close": math.nan, "next_open": 101, "next_close": 102},
        {"t_close": 100, "next_open": 101, "next_close": 102, "suspended": True},
        {"t_close": 100, "next_open": 101, "next_close": 102, "no_trade": True},
        {"t_close": 100, "next_open": 101, "next_close": 102, "next_quality_status": "stale"},
    ]
    for values in cases:
        result = derive_target_labels(adjustment_basis="official_adjusted", **values)
        assert result["quality_status"] == "unavailable"
        assert all(item["label"] is None for item in result["targets"].values())
    wrong_basis = derive_target_labels(
        t_close=100,
        next_open=101,
        next_close=102,
        adjustment_basis="raw_unadjusted",
    )
    assert wrong_basis["quality_status"] == "unavailable"
    assert "adjustment_basis_is_not_official_adjusted" in wrong_basis["reasons"]


def test_sample_identity_is_channel_independent_and_outcome_projection_is_explicit() -> None:
    sample_id = target_sample_id(
        stock_code="2330",
        prediction_trade_date="2026-08-31",
        analysis_cutoff="2026-08-31T13:45:00+08:00",
        regime="normal",
    )
    assert sample_id == target_sample_id(
        stock_code="2330",
        prediction_trade_date="2026-08-31",
        analysis_cutoff="2026-08-31T13:45:00+08:00",
        regime="normal",
    )
    labels = _labels(102, 103)
    rows = build_outcome_rows(
        sample_id=sample_id,
        stock_code="2330",
        prediction_trade_date="2026-08-31",
        outcome_trade_date="2026-09-01",
        outcome_revision="official-adjusted-v1",
        available_at="2026-09-01T14:00:00+08:00",
        recorded_at="2026-09-01T14:01:00+08:00",
        labels=labels,
    )

    assert len(rows) == 3
    assert {row["target_key"] for row in rows} == {
        "next_open_gap",
        "continuation_reversal",
        "next_close_direction",
    }
    assert all(row["adjustment_basis"] == "official_adjusted" for row in rows)
