from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.strategy_backtest import (  # noqa: E402
    _evidence_assessment,
    _metric_row,
    prepare_point_in_time_frame,
)
from services.strategy_backtest_service import render_strategy_backtest_markdown  # noqa: E402


def _history_rows(count: int = 90) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=count)
    rows = []
    for index, trade_date in enumerate(dates):
        close = 100.0 + index * 0.2
        rows.append(
            {
                "trade_date": trade_date.date().isoformat(),
                "code": "2330",
                "name": "台積電",
                "market": "listed",
                "first_seen_date": "1994-09-05",
                "last_seen_date": trade_date.date().isoformat(),
                "open": close - 0.1,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": 2_000_000,
                "volume_unit": "shares",
                "amount": close * 2_000_000,
                "source": "TWSE MI_INDEX",
                "source_quality": "official",
                "formula_version": "test-v1",
                "input_row_count": index + 1,
                "input_start_date": dates[0].date().isoformat(),
                "input_end_date": trade_date.date().isoformat(),
                "adjustment_event_count": 0,
                "ma20": close - 1,
                "ma60": close - 2,
                "rsi14": 38 + (index % 3),
                "macd_osc": -1 + index * 0.01,
                "atr14": 2,
                "volume_ma20": 2_000_000,
                "previous_10d_low": close - 3,
                "technical_data_quality": "ok",
                "technical_decision_ready": 1,
            }
        )
    return pd.DataFrame(rows)


def test_signal_uses_t_close_but_entry_uses_next_session_open() -> None:
    raw = _history_rows()
    raw.loc[60, "open"] = 150.0
    prepared = prepare_point_in_time_frame(
        raw,
        pd.DataFrame(columns=["code", "action_date"]),
    )
    signal = prepared.iloc[59]
    expected = raw.loc[64, "close"] / raw.loc[60, "open"] - 1.0

    assert signal["score_ready"]
    assert signal["return_5d"] == expected
    assert pd.isna(prepared.iloc[-1]["return_5d"])


def test_future_prices_do_not_change_the_signal_score() -> None:
    first = _history_rows()
    second = first.copy()
    second.loc[60:, ["open", "high", "low", "close"]] *= 3
    empty_actions = pd.DataFrame(columns=["code", "action_date"])

    first_score = prepare_point_in_time_frame(first, empty_actions).iloc[59]["prefilter_score"]
    second_score = prepare_point_in_time_frame(second, empty_actions).iloc[59]["prefilter_score"]

    assert first_score == second_score


def test_forward_outcome_crossing_a_known_corporate_action_is_excluded() -> None:
    raw = _history_rows()
    action_date = raw.loc[64, "trade_date"]
    prepared = prepare_point_in_time_frame(
        raw,
        pd.DataFrame(
            [
                {
                    "code": "2330",
                    "action_date": action_date,
                    "action_type": "dividend",
                    "is_confirmed": 1,
                }
            ]
        ),
    )
    signal = prepared.iloc[59]

    assert signal["score_ready"]
    assert bool(signal["contaminated_5d"]) is True
    assert np.isnan(signal["return_5d"])


def test_twenty_day_turnover_gate_is_point_in_time() -> None:
    raw = _history_rows()
    raw.loc[:58, "amount"] = 20_000_000
    prepared = prepare_point_in_time_frame(
        raw,
        pd.DataFrame(columns=["code", "action_date"]),
    )

    assert not bool(prepared.iloc[59]["score_ready"])
    assert prepared.iloc[59]["average_turnover_20d"] < 100_000_000


def test_metric_row_includes_tail_risk_and_diagnostic_drawdown() -> None:
    rows = pd.DataFrame(
        {
            "trade_date": ["2026-01-02", "2026-01-05", "2026-01-06"],
            "code": ["2330", "2317", "2382"],
            "return_5d": [0.10, -0.20, 0.05],
            "excess_return_5d": [0.08, -0.18, 0.03],
            "mae_5d": [-0.02, -0.25, -0.01],
            "mfe_5d": [0.12, 0.01, 0.07],
        }
    )

    metrics = _metric_row(rows, 5)

    assert metrics["p05_return_pct"] < 0
    assert metrics["worst_return_pct"] == -20.0
    assert metrics["diagnostic_max_drawdown_pct"] == -20.0


def test_evidence_assessment_holds_weights_when_test_is_not_monotonic() -> None:
    monotonicity = [
        {
            "panel": "established_universe",
            "sample": "test",
            "horizon_sessions": horizon,
            "spearman_like_correlation": correlation,
            "top_minus_bottom_pct": spread,
        }
        for horizon, correlation, spread in ((5, -0.1, -0.2), (10, 0.1, 0.5), (20, 0.8, 2.0))
    ]
    low_zone_metrics = [
        {
            "cohort": cohort,
            "panel": "established_universe",
            "sample": "test",
            "horizon_sessions": horizon,
            "observations": count,
        }
        for cohort, count in (
            ("full_existing_rule", 6),
            ("full_plus_rsi_delta_1_5", 5),
            ("full_plus_macd_two_days", 2),
            ("full_plus_both_stricter", 1),
        )
        for horizon in (5, 10, 20)
    ]

    evidence = _evidence_assessment(
        monotonicity,
        low_zone_metrics,
        [{"panel": "broad_market", "trade_dates": 2}],
    )

    assert evidence["production_weight_decision"] == "hold"
    assert evidence["ranking_weight_status"] == "not_validated"
    assert evidence["stricter_rule_status"] == "partially_or_fully_insufficient"
    assert evidence["broad_market_coverage_status"] == "insufficient"


def test_evidence_assessment_uses_broad_panel_after_sixty_dates() -> None:
    monotonicity = [
        {
            "panel": "broad_market",
            "sample": sample,
            "horizon_sessions": horizon,
            "spearman_like_correlation": correlation,
            "top_minus_bottom_pct": spread,
        }
        for sample, correlation, spread in (("train", -0.2, -0.1), ("test", 0.8, 1.0))
        for horizon in (5, 10, 20)
    ]
    cohort_counts = {
        "full_existing_rule": 41,
        "full_plus_rsi_delta_1_5": 34,
        "full_plus_macd_two_days": 15,
        "full_plus_both_stricter": 13,
    }
    low_zone_metrics = [
        {
            "cohort": cohort,
            "panel": "broad_market",
            "sample": "test",
            "horizon_sessions": horizon,
            "observations": count,
        }
        for cohort, count in cohort_counts.items()
        for horizon in (5, 10, 20)
    ]

    evidence = _evidence_assessment(
        monotonicity,
        low_zone_metrics,
        [{"panel": "broad_market", "trade_dates": 71}],
    )

    assert evidence["primary_evidence_panel"] == "broad_market"
    assert evidence["full_existing_rule_status"] == "sample_sufficient"
    assert evidence["stricter_rule_statuses"]["full_plus_rsi_delta_1_5"] == "sample_sufficient"
    assert evidence["stricter_rule_statuses"]["full_plus_macd_two_days"] == "insufficient_sample"
    assert evidence["every_rule_sample_gate_status"] == "pending"
    assert evidence["ranking_test_direction_passes"] is True
    assert evidence["ranking_train_direction_passes"] is False
    assert evidence["ranking_weight_status"] == "not_validated"


def test_markdown_reports_existing_rule_sample_gate_accurately() -> None:
    report = {
        "version": "test",
        "generated_at": "2026-08-26T00:00:00+08:00",
        "coverage": [],
        "ranking_validation": {"monotonicity": []},
        "low_zone_validation": {"metrics": [], "regime_metrics": []},
        "evidence_assessment": {
            "ranking_test_results": [],
            "full_existing_rule_status": "sample_sufficient",
            "full_existing_rule_test_observations": {"5d": 97, "10d": 97, "20d": 57},
            "stricter_rule_test_observations": {},
            "broad_market_trade_dates": 133,
            "broad_market_coverage_status": "sufficient",
            "minimum_required_broad_market_dates": 60,
            "minimum_required_test_observations_per_horizon": 30,
        },
    }

    markdown = render_strategy_backtest_markdown(report)

    assert "完整既有進場規則樣本：5d=97筆、10d=97筆、20d=57筆；已通過每個持有期至少 30 筆" in markdown
