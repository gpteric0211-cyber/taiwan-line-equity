from __future__ import annotations

"""Backend-only ordinal magnitude calibration for V11 Shadow evidence.

The calibrator intentionally accepts only already-observed training rows whose
outcomes were available by the declared training cutoff.  It never consumes a
holdout row, never changes a factor weight, and never marks itself released.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from analysis.impact_magnitude_calibration_v1 import (
    IMPACT_MAGNITUDE_CONTRACT_VERSION,
    MAGNITUDE_BINS,
    MAGNITUDE_BIN_VERSION,
)


ORDINAL_MAGNITUDE_CALIBRATOR_VERSION = "OrdinalMagnitudeCalibratorV1"
MAGNITUDE_CLASSES = tuple(MAGNITUDE_BINS)
_SMOOTHING_ALPHA = 1.0


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _aware(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def fit_ordinal_magnitude_calibrator(
    training_rows: Iterable[Mapping[str, Any]],
    *,
    training_cutoff: str,
) -> dict[str, Any]:
    """Fit a sealed empirical ordinal map using training evidence only.

    Laplace smoothing is fixed at one.  This is a deterministic backend
    projection from a five-level ordinal candidate to an outcome distribution;
    it is not a Qwen probability and remains Shadow-only.
    """

    cutoff = _aware(training_cutoff, "training_cutoff")
    counts: dict[str, dict[str, int]] = {
        predicted: {actual: 0 for actual in MAGNITUDE_CLASSES}
        for predicted in MAGNITUDE_CLASSES
    }
    normalized_rows: list[dict[str, Any]] = []
    identities: set[str] = set()
    for source in training_rows:
        row = dict(source)
        identity = str(row.get("training_row_id") or "").strip()
        predicted = str(row.get("predicted_magnitude") or "").strip()
        actual = str(row.get("actual_magnitude") or "").strip()
        if not identity or identity in identities:
            raise ValueError("training_row_id must be present and unique")
        if predicted not in MAGNITUDE_CLASSES or actual not in MAGNITUDE_CLASSES:
            raise ValueError("training magnitude class is invalid")
        if row.get("synthetic") is True:
            raise ValueError("synthetic calibration rows are forbidden")
        prediction_available = _aware(
            row.get("prediction_available_at"), "prediction_available_at"
        )
        outcome_available = _aware(
            row.get("outcome_available_at"), "outcome_available_at"
        )
        if prediction_available >= outcome_available:
            raise ValueError("prediction must be sealed before its outcome is available")
        if outcome_available > cutoff:
            raise ValueError("training outcome is after the training cutoff")
        identities.add(identity)
        counts[predicted][actual] += 1
        normalized_rows.append(
            {
                "training_row_id": identity,
                "predicted_magnitude": predicted,
                "actual_magnitude": actual,
                "prediction_available_at": prediction_available.isoformat(),
                "outcome_available_at": outcome_available.isoformat(),
                "synthetic": False,
            }
        )

    normalized_rows.sort(key=lambda item: item["training_row_id"])
    conditional_probabilities: dict[str, dict[str, float]] = {}
    for predicted in MAGNITUDE_CLASSES:
        denominator = sum(counts[predicted].values()) + (
            _SMOOTHING_ALPHA * len(MAGNITUDE_CLASSES)
        )
        conditional_probabilities[predicted] = {
            actual: (counts[predicted][actual] + _SMOOTHING_ALPHA) / denominator
            for actual in MAGNITUDE_CLASSES
        }

    training_digest = _digest(
        {
            "contract_version": ORDINAL_MAGNITUDE_CALIBRATOR_VERSION,
            "training_cutoff": cutoff.isoformat(),
            "rows": normalized_rows,
        }
    )
    complete_training_buckets = all(sum(bucket.values()) > 0 for bucket in counts.values())
    artifact_payload = {
        "contract_version": ORDINAL_MAGNITUDE_CALIBRATOR_VERSION,
        "impact_magnitude_contract_version": IMPACT_MAGNITUDE_CONTRACT_VERSION,
        "magnitude_bin_version": MAGNITUDE_BIN_VERSION,
        "method": "conditional_empirical_laplace_alpha_1",
        "training_cutoff": cutoff.isoformat(),
        "training_row_count": len(normalized_rows),
        "training_digest": training_digest,
        "class_counts": counts,
        "conditional_probabilities": conditional_probabilities,
        "calibration_state": (
            "shadow_only" if complete_training_buckets else "shadow_insufficient_training"
        ),
        "released": False,
        "formal_weight": 0.0,
        "eligible_for_weight": False,
    }
    return {**artifact_payload, "calibrator_digest": _digest(artifact_payload)}


def calibrated_magnitude_probabilities(
    calibrator: Mapping[str, Any],
    *,
    predicted_magnitude: str,
) -> dict[str, Any]:
    """Project one ordinal candidate through a sealed backend artifact."""

    artifact = dict(calibrator)
    supplied_digest = str(artifact.pop("calibrator_digest", ""))
    if supplied_digest != _digest(artifact):
        raise ValueError("calibrator_digest does not match the artifact")
    if artifact.get("contract_version") != ORDINAL_MAGNITUDE_CALIBRATOR_VERSION:
        raise ValueError("calibrator contract version is invalid")
    if artifact.get("released") is not False or artifact.get("formal_weight") != 0.0:
        raise ValueError("Stage 6 calibrator must remain Shadow with zero weight")
    if artifact.get("calibration_state") != "shadow_only":
        raise ValueError("calibrator training buckets are incomplete")
    label = str(predicted_magnitude or "")
    if label not in MAGNITUDE_CLASSES:
        raise ValueError("predicted_magnitude is invalid")
    distributions = artifact.get("conditional_probabilities")
    if not isinstance(distributions, Mapping):
        raise ValueError("calibrator probability map is invalid")
    probabilities = distributions.get(label)
    if not isinstance(probabilities, Mapping) or set(probabilities) != set(
        MAGNITUDE_CLASSES
    ):
        raise ValueError("calibrator probability bucket is invalid")
    return {
        "calibrator_version": ORDINAL_MAGNITUDE_CALIBRATOR_VERSION,
        "calibrator_digest": supplied_digest,
        "magnitude_bin_version": MAGNITUDE_BIN_VERSION,
        "predicted_magnitude": label,
        "magnitude_probabilities": {
            key: float(probabilities[key]) for key in MAGNITUDE_CLASSES
        },
        "calibration_state": "shadow_calibrated_backend",
        "released": False,
        "formal_weight": 0.0,
        "eligible_for_weight": False,
    }
