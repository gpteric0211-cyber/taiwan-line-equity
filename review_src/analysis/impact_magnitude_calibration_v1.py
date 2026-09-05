from __future__ import annotations

"""V11 ordinal impact-magnitude and calibration-boundary contracts.

The module deliberately does not fit a volatility estimator or a probability
calibrator.  It validates caller-supplied, cutoff-safe sigma evidence and
classifies the approved ordinal bins.  Qwen output is normalized separately so
raw model scores can never masquerade as backend-calibrated probabilities.
"""

from datetime import datetime
import math
import re
from typing import Any, Mapping


IMPACT_MAGNITUDE_CONTRACT_VERSION = "ImpactMagnitudeAndCalibrationContractV1"
MAGNITUDE_BIN_VERSION = "MagnitudeBinsAbsZV1"
MAGNITUDE_BINS = {
    "negligible": (0.0, 0.5),
    "low": (0.5, 1.0),
    "medium": (1.0, 2.0),
    "high": (2.0, 3.0),
    "extreme": (3.0, None),
}
TARGET_REALIZED_RETURN_SPECS = {
    "NEXT_SESSION_OPEN_GAP": {
        "formula_version": "NextSessionOpenGapReturnV1",
        "formula": "next_open / t_close - 1",
    },
    "NEXT_SESSION_OPEN_TO_CLOSE_CONTINUATION": {
        "formula_version": "NextSessionOpenToCloseReturnV1",
        "formula": "next_close / next_open - 1",
    },
    "NEXT_SESSION_CLOSE_DIRECTION": {
        "formula_version": "NextSessionCloseReturnV1",
        "formula": "next_close / t_close - 1",
    },
}

_MAGNITUDE_LEVELS = set(MAGNITUDE_BINS) | {"unknown_pending"}
_TARGET_DIRECTIONS = {"positive", "negative", "mixed", "neutral", "unknown"}
_TARGET_MATERIALITY = {"critical", "high", "medium", "low", "unknown_pending"}
_TARGET_RELATIONSHIPS = {
    "direct_company",
    "parent_subsidiary_group",
    "supply_chain",
    "customer",
    "peer",
    "incidental_mention",
    "unresolved",
}
_REGIMES = {"normal", "material_event", "material_pending", "suppressed_pending"}
_PRICED_IN_STATES = {"already_priced", "still_developing", "mixed", "unknown"}
_FORBIDDEN_MODEL_KEYS = {
    "direction_probabilities",
    "magnitude_probabilities",
    "expected_percent_range",
    "expected_price_change",
    "future_price",
    "predicted_return",
    "predicted_return_pct",
    "price_target",
    "target_price",
}
_PROMPT_INJECTION_FOLLOWING = re.compile(
    r"(?:ignore|忽略|override|覆寫).{0,30}(?:system|developer|instruction|規則|指令)",
    re.IGNORECASE,
)


def _aware(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _unavailable(forecast_target_id: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "contract_version": IMPACT_MAGNITUDE_CONTRACT_VERSION,
        "magnitude_bin_version": MAGNITUDE_BIN_VERSION,
        "forecast_target_id": forecast_target_id,
        "status": "unavailable",
        "reason_codes": list(dict.fromkeys(reasons)),
        "target_impact_magnitude": "unknown_pending",
        "absolute_z": None,
        "calibration_state": "unreleased",
        "magnitude_probabilities": None,
        "formal_direction_weight": 0.0,
        "eligible_for_weight": False,
    }


def classify_realized_impact_magnitude(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a realized target return using only complete cutoff-safe sigma evidence."""

    forecast_target_id = str(evidence.get("forecast_target_id") or "")
    spec = TARGET_REALIZED_RETURN_SPECS.get(forecast_target_id)
    if spec is None:
        raise ValueError("forecast_target_id is outside TargetLabelContractV1")

    reasons: list[str] = []
    cutoff = _aware(evidence.get("analysis_cutoff"), "analysis_cutoff")
    sigma_available = _aware(evidence.get("sigma_available_at"), "sigma_available_at")
    if sigma_available > cutoff:
        reasons.append("sigma_available_after_analysis_cutoff")
    if evidence.get("realized_return_formula_version") != spec["formula_version"]:
        reasons.append("realized_return_formula_version_mismatch")
    if str(evidence.get("return_basis") or "") not in {"raw", "benchmark_adjusted"}:
        reasons.append("return_basis_is_invalid")
    if evidence.get("corporate_action_adjustment_state") != "official_adjusted":
        reasons.append("corporate_action_adjustment_is_not_official")
    if str(evidence.get("market_handling_state") or "") != "normal":
        reasons.append("limit_suspension_or_no_trade_requires_unavailable")
    for field in (
        "sigma_estimator_version",
        "winsorization_version",
        "outlier_handling_version",
        "sigma_formula_version",
    ):
        if not str(evidence.get(field) or "").strip():
            reasons.append(f"{field}_is_missing")

    try:
        lookback = int(evidence.get("rolling_lookback") or 0)
        minimum = int(evidence.get("minimum_observations") or 0)
        observed = int(evidence.get("observation_count") or 0)
    except (TypeError, ValueError):
        lookback = minimum = observed = 0
    if minimum < 1 or lookback < minimum:
        reasons.append("volatility_window_contract_is_invalid")
    if observed < minimum:
        reasons.append("volatility_observations_are_insufficient")

    target_return = _finite(evidence.get("realized_target_return"), "realized_target_return")
    sigma = _finite(evidence.get("sigma_asof"), "sigma_asof")
    floor = _finite(evidence.get("volatility_floor"), "volatility_floor")
    if sigma <= 0 or floor <= 0 or sigma < floor:
        reasons.append("sigma_or_volatility_floor_is_invalid")
    if reasons:
        return _unavailable(forecast_target_id, reasons)

    absolute_z = abs(target_return) / sigma
    if absolute_z < 0.5:
        level = "negligible"
    elif absolute_z < 1.0:
        level = "low"
    elif absolute_z < 2.0:
        level = "medium"
    elif absolute_z < 3.0:
        level = "high"
    else:
        level = "extreme"
    return {
        "contract_version": IMPACT_MAGNITUDE_CONTRACT_VERSION,
        "magnitude_bin_version": MAGNITUDE_BIN_VERSION,
        "forecast_target_id": forecast_target_id,
        "realized_return_formula_version": spec["formula_version"],
        "sigma_estimator_version": str(evidence["sigma_estimator_version"]),
        "sigma_formula_version": str(evidence["sigma_formula_version"]),
        "sigma_available_at": sigma_available.isoformat(),
        "analysis_cutoff": cutoff.isoformat(),
        "status": "shadow_only",
        "reason_codes": [],
        "target_impact_magnitude": level,
        "absolute_z": absolute_z,
        "calibration_state": "unreleased",
        "magnitude_probabilities": None,
        "formal_direction_weight": 0.0,
        "eligible_for_weight": False,
    }


def _find_forbidden_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_MODEL_KEYS:
                return normalized
            found = _find_forbidden_key(nested)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _find_forbidden_key(nested)
            if found is not None:
                return found
    return None


def _bounded_strings(value: Any, field: str, *, maximum: int = 12) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise ValueError(f"{field} must be a bounded list")
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text or len(text) > 500:
            raise ValueError(f"{field} contains invalid text")
        if _PROMPT_INJECTION_FOLLOWING.search(text):
            raise ValueError("prompt_injection_following")
        result.append(text)
    return result


def normalize_qwen_target_impact_candidate(
    candidate: Mapping[str, Any],
    *,
    deterministic_context: Mapping[str, Any],
    allowed_evidence_ids: set[str],
) -> dict[str, Any]:
    """Normalize explanation-only Qwen output without inventing probabilities."""

    payload = dict(candidate)
    forbidden = _find_forbidden_key(payload)
    if forbidden is not None:
        raise ValueError(f"Qwen target candidate contains prohibited field: {forbidden}")
    required = {
        "target_direction",
        "target_impact_magnitude",
        "drivers",
        "counterevidence",
        "uncertainty",
        "priced_in_state",
        "evidence_ids",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"Qwen target candidate missing fields: {','.join(missing)}")

    target_direction = str(payload["target_direction"])
    magnitude = str(payload["target_impact_magnitude"])
    priced_in = str(payload["priced_in_state"])
    if target_direction not in _TARGET_DIRECTIONS:
        raise ValueError("target_direction is invalid")
    if magnitude not in _MAGNITUDE_LEVELS:
        raise ValueError("target_impact_magnitude is invalid")
    if priced_in not in _PRICED_IN_STATES:
        raise ValueError("priced_in_state is invalid")

    evidence_ids = _bounded_strings(payload["evidence_ids"], "evidence_ids")
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("evidence_ids must not contain duplicates")
    if not evidence_ids or not set(evidence_ids) <= set(allowed_evidence_ids):
        raise ValueError("Qwen target candidate references unknown evidence")

    target_materiality = str(deterministic_context.get("target_materiality") or "")
    relationship = str(deterministic_context.get("target_relationship_type") or "")
    regime = str(deterministic_context.get("regime_selection") or "")
    if target_materiality not in _TARGET_MATERIALITY:
        raise ValueError("deterministic target_materiality is invalid")
    if relationship not in _TARGET_RELATIONSHIPS:
        raise ValueError("deterministic target_relationship_type is invalid")
    if regime not in _REGIMES:
        raise ValueError("deterministic regime_selection is invalid")

    internal_scores = payload.get("uncalibrated_internal_scores")
    internal_audit: dict[str, float] | None = None
    if internal_scores is not None:
        if not isinstance(internal_scores, Mapping) or set(internal_scores) != set(MAGNITUDE_BINS):
            raise ValueError("uncalibrated_internal_scores has invalid keys")
        internal_audit = {
            str(key): _finite(value, "uncalibrated_internal_scores")
            for key, value in internal_scores.items()
        }

    return {
        "contract_version": IMPACT_MAGNITUDE_CONTRACT_VERSION,
        "repository_result": {
            "target_materiality": target_materiality,
            "target_direction": target_direction,
            "target_impact_magnitude": magnitude,
            "regime_selection": regime,
            "target_relationship_type": relationship,
            "drivers": _bounded_strings(payload["drivers"], "drivers"),
            "counterevidence": _bounded_strings(payload["counterevidence"], "counterevidence"),
            "uncertainty": _bounded_strings(payload["uncertainty"], "uncertainty"),
            "priced_in_state": priced_in,
            "evidence_ids": evidence_ids,
            "eligible_for_explanation": True,
            "eligible_for_weight": False,
            "calibration_state": "shadow",
        },
        "internal_audit": {
            "score_semantics": "uncalibrated_internal" if internal_audit is not None else "not_retained",
            "scores": internal_audit,
            "publicly_displayable": False,
            "eligible_for_weight": False,
        },
    }
