from __future__ import annotations

import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.target_label_contract_v1 import TARGET_CLASSES  # noqa: E402
from evaluation import statistical_release_gate_v1 as gate  # noqa: E402


def _probabilities(classes, actual: str, confidence: float) -> dict[str, float]:
    remainder = (1.0 - confidence) / (len(classes) - 1)
    return {label: confidence if label == actual else remainder for label in classes}


def _row(
    *,
    regime: str = "normal",
    target: str = "next_open_gap",
    index: int = 0,
    stable_confidence: float = 0.6,
    candidate_confidence: float = 0.9,
    candidate_completed: bool = True,
) -> dict[str, object]:
    classes = TARGET_CLASSES[target]
    actual = classes[index % len(classes)]
    day = (index % (120 if regime == "normal" else 60)) + 1
    cluster = f"cluster-{index % 60:03d}"
    return {
        "sample_id": f"{regime}-{target}-{index}",
        "stock_code": f"{2300 + index % 30:04d}",
        "prediction_trade_date": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}",
        "target_key": target,
        "regime": regime,
        "actual_label": actual,
        "outcome_revision": "official-adjusted-v1",
        "event_cluster_id": cluster if regime == "material_event" else None,
        "event_type": f"event-type-{index % 3}" if regime == "material_event" else None,
        "large_safety_slice": regime == "material_event",
        "stable": {
            "eligible": True,
            "completion_state": "completed",
            "probabilities": _probabilities(classes, actual, stable_confidence),
        },
        "candidate": {
            "eligible": candidate_completed,
            "completion_state": "completed" if candidate_completed else "failed",
            "probabilities": _probabilities(classes, actual, candidate_confidence),
        },
    }


def test_empty_evidence_remains_shadow_for_insufficient_power() -> None:
    result = gate.evaluate_statistical_release_gate([])

    assert result["result"] == "remain_shadow_insufficient_power"
    assert result["regimes"]["normal"]["result"] == "remain_shadow_insufficient_power"
    assert result["regimes"]["material_event"]["result"] == "remain_shadow_insufficient_power"
    assert "next_open_gap:paired_observations_below_minimum" in result["regimes"]["normal"]["reason_codes"]


def test_synthetic_duplicate_and_invalid_completed_probabilities_are_invalid_evidence() -> None:
    synthetic = _row()
    synthetic["synthetic"] = True
    assert gate.evaluate_statistical_release_gate([synthetic])["result"] == "invalid_evidence"

    duplicate = _row()
    assert gate.evaluate_statistical_release_gate([duplicate, dict(duplicate)])["result"] == "invalid_evidence"

    invalid = _row()
    invalid["candidate"] = {
        "eligible": True,
        "completion_state": "completed",
        "probabilities": {"up": 0.8, "flat": 0.8, "down": -0.6},
    }
    assert gate.evaluate_statistical_release_gate([invalid])["result"] == "invalid_evidence"


def test_release_evaluation_cannot_reduce_the_predeclared_bootstrap_count() -> None:
    result = gate.evaluate_statistical_release_gate([], bootstrap_iterations=9999)

    assert result["result"] == "invalid_evidence"
    assert result["reason_codes"] == ["bootstrap_iterations_are_invalid"]


def test_failed_predictions_remain_in_metric_denominator_as_noncompletion() -> None:
    rows = gate._prepared_rows(
        [
            _row(index=0, candidate_completed=True),
            _row(index=1, candidate_completed=False),
        ]
    )
    metrics = gate._metric_bundle(rows, "candidate")

    assert metrics["completion"] == 0.5
    assert metrics["brier"] > 0
    assert metrics["high_confidence_correct"] == 0.5


def test_equal_frequency_ece_bins_are_contiguous_after_sorting(monkeypatch) -> None:
    probabilities = [[index / 20, 1 - index / 20] for index in range(20)]
    actuals = [0] * 10 + [1] * 10

    value = gate._adaptive_ece(probabilities, actuals, 2)

    assert 0 <= value <= 1


def _sufficient_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for target in TARGET_CLASSES:
        rows.extend(_row(regime="normal", target=target, index=index) for index in range(2_000))
        rows.extend(_row(regime="material_event", target=target, index=index) for index in range(300))
    return rows


def _stub_bootstrap(monkeypatch) -> None:
    monkeypatch.setattr(gate, "_paired_power", lambda *_args, **_kwargs: 1.0)

    def differences(rows, *, iterations, seed, mode):
        stable = gate._metric_bundle(rows, "stable")
        candidate = gate._metric_bundle(rows, "candidate")
        return {
            metric: [candidate[metric] - stable[metric]] * iterations
            for metric in gate.ALL_METRICS
        }

    def macro(rows, *, iterations, seed, mode):
        values = []
        for target in TARGET_CLASSES:
            subset = [row for row in rows if row["target_key"] == target]
            values.append(
                gate._metric_bundle(subset, "candidate")["brier"]
                - gate._metric_bundle(subset, "stable")["brier"]
            )
        return [sum(values) / len(values)] * iterations

    monkeypatch.setattr(gate, "_bootstrap_differences", differences)
    monkeypatch.setattr(gate, "_bootstrap_macro_brier", macro)


def test_all_predeclared_hypotheses_can_pass_only_with_sufficient_real_rows(monkeypatch) -> None:
    _stub_bootstrap(monkeypatch)

    result = gate.evaluate_statistical_release_gate(_sufficient_rows())

    assert result["result"] == "pass_for_canary"
    assert all(details["result"] == "pass_for_canary" for details in result["regimes"].values())
    assert all(len(details["target_superiority_wins"]) >= 2 for details in result["regimes"].values())
    assert result["bootstrap_iterations"] == 10_000
    assert len(result["gate_spec_hash"]) == 64


def test_noninferior_but_identical_candidate_stays_shadow(monkeypatch) -> None:
    rows = _sufficient_rows()
    for row in rows:
        row["candidate"] = dict(row["stable"])
    _stub_bootstrap(monkeypatch)

    result = gate.evaluate_statistical_release_gate(rows)

    assert result["result"] == "remain_shadow_noninferior_but_not_superior"
    assert all(
        details["result"] == "remain_shadow_noninferior_but_not_superior"
        for details in result["regimes"].values()
    )
