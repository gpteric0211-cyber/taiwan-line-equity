from __future__ import annotations

import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from analysis.ordinal_magnitude_calibrator_v1 import (  # noqa: E402
    MAGNITUDE_CLASSES,
    calibrated_magnitude_probabilities,
    fit_ordinal_magnitude_calibrator,
)
from evaluation.prospective_release_evidence_v1 import (  # noqa: E402
    HARD_SAFETY_GATES,
    _magnitude_metrics,
    build_denominator_manifest,
    evaluate_prospective_release_gate,
    member_id,
)


def _training_row(index: int, *, outcome_at: str = "2026-08-31T14:00:00+08:00") -> dict:
    label = MAGNITUDE_CLASSES[index % len(MAGNITUDE_CLASSES)]
    return {
        "training_row_id": f"training-{index}",
        "predicted_magnitude": label,
        "actual_magnitude": label,
        "prediction_available_at": "2026-08-30T07:00:00+08:00",
        "outcome_available_at": outcome_at,
        "synthetic": False,
    }


def test_backend_calibrator_is_cutoff_safe_deterministic_and_zero_weight() -> None:
    rows = [_training_row(index) for index in range(10)]
    first = fit_ordinal_magnitude_calibrator(
        rows, training_cutoff="2026-09-01T00:00:00+08:00"
    )
    second = fit_ordinal_magnitude_calibrator(
        reversed(rows), training_cutoff="2026-09-01T00:00:00+08:00"
    )

    assert first == second
    assert first["released"] is False
    assert first["formal_weight"] == 0.0
    projection = calibrated_magnitude_probabilities(
        first, predicted_magnitude="high"
    )
    assert projection["calibration_state"] == "shadow_calibrated_backend"
    assert set(projection["magnitude_probabilities"]) == set(MAGNITUDE_CLASSES)
    assert sum(projection["magnitude_probabilities"].values()) == pytest.approx(1.0)


def test_backend_calibrator_rejects_future_outcomes_and_synthetic_rows() -> None:
    with pytest.raises(ValueError, match="after the training cutoff"):
        fit_ordinal_magnitude_calibrator(
            [_training_row(0, outcome_at="2026-09-02T14:00:00+08:00")],
            training_cutoff="2026-09-01T00:00:00+08:00",
        )
    with pytest.raises(ValueError, match="synthetic"):
        fit_ordinal_magnitude_calibrator(
            [{**_training_row(0), "synthetic": True}],
            training_cutoff="2026-09-01T00:00:00+08:00",
        )

    sparse = fit_ordinal_magnitude_calibrator(
        [_training_row(0)], training_cutoff="2026-09-01T00:00:00+08:00"
    )
    assert sparse["calibration_state"] == "shadow_insufficient_training"
    with pytest.raises(ValueError, match="training buckets are incomplete"):
        calibrated_magnitude_probabilities(sparse, predicted_magnitude="negligible")


def _probabilities(actual: str, confidence: float) -> dict[str, float]:
    remainder = (1.0 - confidence) / (len(MAGNITUDE_CLASSES) - 1)
    return {
        label: confidence if label == actual else remainder
        for label in MAGNITUDE_CLASSES
    }


def _row(*, collection_mode: str = "prospective_shadow") -> tuple[dict, dict]:
    sample_id = "sample-2454-20260901"
    target = "next_open_gap"
    identity_id = member_id(sample_id, target)
    denominator = build_denominator_manifest(eligible_member_ids=[identity_id])
    pair_identity = {
        "stock_code": "2454",
        "target_key": target,
        "analysis_cutoff": "2026-09-01T06:45:00+08:00",
        "calendar_revision": "twse-calendar-2026-v1",
        "event_cluster_id": None,
        "event_revision_id": None,
        "outcome_revision": "official-adjusted-v1",
        "eligibility_manifest_digest": denominator["denominator_digest"],
    }
    hard_gates = {key: 0 for key in HARD_SAFETY_GATES}
    role = {
        "eligible": True,
        "completion_state": "completed",
        "probabilities": {"up": 0.6, "flat": 0.3, "down": 0.1},
        "evidence_identity": pair_identity,
        "collection_mode": collection_mode,
        "prediction_sealed_at": "2026-09-01T06:45:01+08:00",
        "model_training_cutoff": None,
        "walk_forward_train_end_at": None,
        "magnitude_probabilities": _probabilities("high", 0.6),
        "magnitude_calibration_state": "shadow_calibrated_backend",
        "hard_safety_gate_counts": hard_gates,
    }
    row = {
        "sample_id": sample_id,
        "stock_code": "2454",
        "prediction_trade_date": "2026-09-01",
        "target_key": target,
        "regime": "normal",
        "actual_label": "up",
        "actual_magnitude_label": "high",
        "analysis_cutoff": "2026-09-01T06:45:00+08:00",
        "outcome_available_at": "2026-09-02T14:00:00+08:00",
        "outcome_revision": "official-adjusted-v1",
        "calendar_revision": "twse-calendar-2026-v1",
        "event_cluster_id": None,
        "event_revision_id": None,
        "event_type": None,
        "large_safety_slice": False,
        "synthetic": False,
        "stable": dict(role),
        "candidate": dict(role),
    }
    return row, denominator


def test_prospective_evidence_with_real_but_small_denominator_remains_shadow() -> None:
    row, denominator = _row()

    result = evaluate_prospective_release_gate(
        [row], denominator_manifest=denominator
    )

    assert result["result"] == "remain_shadow_insufficient_power"
    assert result["denominator_shrinkage"] == 0
    assert result["formal_weight"] == 0.0
    assert result["released"] is False


def test_pair_revision_mismatch_and_denominator_shrinkage_are_invalid() -> None:
    row, denominator = _row()
    candidate = dict(row["candidate"])
    candidate["evidence_identity"] = {
        **candidate["evidence_identity"],
        "calendar_revision": "different-calendar",
    }
    mismatch = {**row, "candidate": candidate}
    assert evaluate_prospective_release_gate(
        [mismatch], denominator_manifest=denominator
    )["result"] == "invalid_evidence"

    empty = evaluate_prospective_release_gate([], denominator_manifest=denominator)
    assert empty["result"] == "invalid_evidence"
    assert empty["reason_codes"] == ["denominator_shrinkage_or_eligibility_mismatch"]


def test_unproven_historical_replay_is_pipeline_only_shadow() -> None:
    row, denominator = _row(collection_mode="historical_replay")

    result = evaluate_prospective_release_gate(
        [row], denominator_manifest=denominator
    )

    assert result["result"] == "remain_shadow_insufficient_power"
    assert any("historical_training_cutoff_unproven" in code for code in result["reason_codes"])


def test_hard_safety_gate_rejects_even_when_direction_gate_is_stubbed(monkeypatch) -> None:
    row, denominator = _row()
    candidate = dict(row["candidate"])
    candidate["hard_safety_gate_counts"] = {
        **candidate["hard_safety_gate_counts"],
        "wrong_entity": 1,
    }
    row = {**row, "candidate": candidate}
    monkeypatch.setattr(
        "evaluation.prospective_release_evidence_v1.direction_gate.evaluate_statistical_release_gate",
        lambda *_args, **_kwargs: {
            "result": "pass_for_canary",
            "reason_codes": [],
            "regimes": {
                "normal": {"result": "pass_for_canary", "reason_codes": []},
                "material_event": {
                    "result": "remain_shadow_insufficient_power",
                    "reason_codes": [],
                },
            },
        },
    )

    result = evaluate_prospective_release_gate(
        [row], denominator_manifest=denominator
    )

    assert result["regimes"]["normal"]["result"] == "reject_safety_regression"


def test_magnitude_metrics_are_ordinal_and_high_impact_aware() -> None:
    row, _ = _row()
    prepared = {
        **row,
        "actual_magnitude_index": MAGNITUDE_CLASSES.index("high"),
        "stable_magnitude_probabilities": list(
            _probabilities("negligible", 0.6).values()
        ),
        "candidate_magnitude_probabilities": list(_probabilities("high", 0.9).values()),
    }

    stable = _magnitude_metrics([prepared], "stable")
    candidate = _magnitude_metrics([prepared], "candidate")

    assert candidate["ranked_probability_score"] < stable["ranked_probability_score"]
    assert candidate["ordinal_log_loss"] < stable["ordinal_log_loss"]
    assert candidate["high_impact_recall"] == 1.0
    assert stable["high_impact_recall"] == 0.0
