from __future__ import annotations

"""V11 prospective-evidence envelope around StatisticalReleaseGateV1.

The established direction gate remains the sole statistical implementation.
This module adds the evidence identities, denominator closure, temporal guards,
hard-safety counts, and ordinal-magnitude metrics required by V11.  Legacy or
unproven rows may be inspected, but can never be promoted by this envelope.
"""

import hashlib
import json
import math
import random
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from analysis.ordinal_magnitude_calibrator_v1 import MAGNITUDE_CLASSES
from evaluation import statistical_release_gate_v1 as direction_gate


PROSPECTIVE_EVIDENCE_CONTRACT_VERSION = "ProspectivePredictionEvidenceContractV1"
DENOMINATOR_CONTRACT_VERSION = "PairedPredictionDenominatorV1"
HARD_SAFETY_GATES = (
    "leakage",
    "wrong_entity",
    "unverified_event_entering_weight",
    "ungrounded_claim",
    "referee_override",
)
VALID_DECISIONS = {
    "explanation_only_canary",
    "predictive_canary",
    "remain_shadow_insufficient_power",
    "remain_shadow_noninferior_but_not_superior",
    "reject_safety_regression",
    "reject_statistical_regression",
    "invalid_evidence",
}
_PAIR_FIELDS = (
    "stock_code",
    "target_key",
    "analysis_cutoff",
    "calendar_revision",
    "event_cluster_id",
    "event_revision_id",
    "outcome_revision",
    "eligibility_manifest_digest",
)
_LOWER_MAGNITUDE_METRICS = {"ranked_probability_score", "ordinal_log_loss"}


class ProspectiveEvidenceError(ValueError):
    pass


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
        raise ProspectiveEvidenceError(f"{field}_is_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveEvidenceError(f"{field}_is_not_offset_aware")
    return parsed.astimezone(timezone.utc)


def member_id(sample_id: Any, target_key: Any) -> str:
    sample = str(sample_id or "").strip()
    target = str(target_key or "").strip()
    if not sample or target not in direction_gate.TARGET_CLASSES:
        raise ProspectiveEvidenceError("denominator_member_identity_is_invalid")
    return _digest({"sample_id": sample, "target_key": target})


def build_denominator_manifest(
    *,
    eligible_member_ids: Iterable[str],
    excluded_members: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Seal eligible and explicitly excluded identities into one denominator."""

    eligible = sorted(str(value or "").strip() for value in eligible_member_ids)
    if any(not value for value in eligible) or len(set(eligible)) != len(eligible):
        raise ProspectiveEvidenceError("eligible_member_ids_are_invalid")
    excluded: list[dict[str, str]] = []
    for source in excluded_members:
        identity = str(source.get("member_id") or "").strip()
        reason = str(source.get("reason") or "").strip()
        if not identity or not reason:
            raise ProspectiveEvidenceError("excluded_member_is_invalid")
        excluded.append({"member_id": identity, "reason": reason})
    excluded.sort(key=lambda item: item["member_id"])
    excluded_ids = [item["member_id"] for item in excluded]
    if len(set(excluded_ids)) != len(excluded_ids) or set(eligible) & set(excluded_ids):
        raise ProspectiveEvidenceError("denominator_member_ids_overlap_or_repeat")
    payload = {
        "contract_version": DENOMINATOR_CONTRACT_VERSION,
        "eligible_member_ids": eligible,
        "excluded_members": excluded,
        "denominator_count": len(eligible) + len(excluded),
    }
    return {**payload, "denominator_digest": _digest(payload)}


def _validated_denominator(manifest: Mapping[str, Any]) -> dict[str, Any]:
    supplied = dict(manifest)
    digest = str(supplied.pop("denominator_digest", ""))
    if supplied.get("contract_version") != DENOMINATOR_CONTRACT_VERSION:
        raise ProspectiveEvidenceError("denominator_contract_version_is_invalid")
    if digest != _digest(supplied):
        raise ProspectiveEvidenceError("denominator_digest_mismatch")
    rebuilt = build_denominator_manifest(
        eligible_member_ids=supplied.get("eligible_member_ids") or (),
        excluded_members=supplied.get("excluded_members") or (),
    )
    if rebuilt != {**supplied, "denominator_digest": digest}:
        raise ProspectiveEvidenceError("denominator_manifest_is_not_canonical")
    return rebuilt


def _probabilities(value: Any) -> list[float] | None:
    if not isinstance(value, Mapping) or set(value) != set(MAGNITUDE_CLASSES):
        return None
    result: list[float] = []
    for label in MAGNITUDE_CLASSES:
        raw = value[label]
        if isinstance(raw, bool):
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or not 0 <= number <= 1:
            return None
        result.append(number)
    return result if math.isclose(sum(result), 1.0, abs_tol=1e-6) else None


def _hard_gate_counts(role: Mapping[str, Any]) -> dict[str, int] | None:
    supplied = role.get("hard_safety_gate_counts")
    if not isinstance(supplied, Mapping) or set(supplied) != set(HARD_SAFETY_GATES):
        return None
    result: dict[str, int] = {}
    for key in HARD_SAFETY_GATES:
        value = supplied[key]
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        if number < 0 or number != value:
            return None
        result[key] = number
    return result


def _validate_pair(
    row: Mapping[str, Any],
    *,
    denominator_digest: str,
) -> tuple[dict[str, Any], list[str]]:
    normalized = dict(row)
    stable = normalized.get("stable")
    candidate = normalized.get("candidate")
    if not isinstance(stable, Mapping) or not isinstance(candidate, Mapping):
        raise ProspectiveEvidenceError("paired_prediction_payload_is_missing")
    stable_identity = stable.get("evidence_identity")
    candidate_identity = candidate.get("evidence_identity")
    if not isinstance(stable_identity, Mapping) or not isinstance(
        candidate_identity, Mapping
    ):
        raise ProspectiveEvidenceError("paired_evidence_identity_is_missing")
    stable_identity = dict(stable_identity)
    candidate_identity = dict(candidate_identity)
    if stable_identity != candidate_identity:
        raise ProspectiveEvidenceError("stable_candidate_evidence_identity_mismatch")
    if set(stable_identity) != set(_PAIR_FIELDS):
        raise ProspectiveEvidenceError("paired_evidence_identity_fields_are_invalid")
    for field in _PAIR_FIELDS:
        expected = (
            denominator_digest
            if field == "eligibility_manifest_digest"
            else normalized.get(field)
        )
        if stable_identity.get(field) != expected:
            raise ProspectiveEvidenceError(f"paired_evidence_identity_mismatch:{field}")
    if str(normalized.get("calendar_revision") or "") in {"", "unproven"}:
        raise ProspectiveEvidenceError("calendar_revision_is_unproven")
    regime = str(normalized.get("regime") or "")
    if regime == "material_event" and (
        not normalized.get("event_cluster_id") or not normalized.get("event_revision_id")
    ):
        raise ProspectiveEvidenceError("material_event_revision_identity_is_missing")
    analysis_cutoff = _aware(normalized.get("analysis_cutoff"), "analysis_cutoff")
    outcome_available = _aware(
        normalized.get("outcome_available_at"), "outcome_available_at"
    )
    if outcome_available <= analysis_cutoff:
        raise ProspectiveEvidenceError("outcome_is_not_after_analysis_cutoff")

    proof_reasons: list[str] = []
    for role_name, role_source in (("stable", stable), ("candidate", candidate)):
        role = dict(role_source)
        collection_mode = str(role.get("collection_mode") or "")
        sealed_at = _aware(
            role.get("prediction_sealed_at"), f"{role_name}_prediction_sealed_at"
        )
        if sealed_at < analysis_cutoff:
            raise ProspectiveEvidenceError(
                f"{role_name}_prediction_sealed_before_analysis_cutoff"
            )
        if collection_mode == "prospective_shadow":
            if sealed_at >= outcome_available:
                raise ProspectiveEvidenceError(
                    f"{role_name}_prospective_prediction_not_sealed_before_outcome"
                )
        elif collection_mode == "historical_replay":
            training_cutoff = role.get("model_training_cutoff")
            walk_forward_end = role.get("walk_forward_train_end_at")
            if not training_cutoff or not walk_forward_end:
                proof_reasons.append(f"{role_name}_historical_training_cutoff_unproven")
            else:
                if _aware(training_cutoff, f"{role_name}_model_training_cutoff") >= analysis_cutoff:
                    raise ProspectiveEvidenceError(
                        f"{role_name}_model_training_cutoff_leakage"
                    )
                if _aware(walk_forward_end, f"{role_name}_walk_forward_train_end_at") >= analysis_cutoff:
                    raise ProspectiveEvidenceError(
                        f"{role_name}_walk_forward_cutoff_leakage"
                    )
        else:
            proof_reasons.append(f"{role_name}_collection_mode_unproven")
    return normalized, proof_reasons


def _magnitude_prepared(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    prepared: list[dict[str, Any]] = []
    reasons: list[str] = []
    for row in rows:
        actual = str(row.get("actual_magnitude_label") or "")
        stable = row.get("stable") if isinstance(row.get("stable"), Mapping) else {}
        candidate = row.get("candidate") if isinstance(row.get("candidate"), Mapping) else {}
        stable_probs = _probabilities(stable.get("magnitude_probabilities"))
        candidate_probs = _probabilities(candidate.get("magnitude_probabilities"))
        if actual not in MAGNITUDE_CLASSES:
            reasons.append(f"{member_id(row.get('sample_id'), row.get('target_key'))}:actual_magnitude_missing")
            continue
        if stable_probs is None or candidate_probs is None:
            reasons.append(f"{member_id(row.get('sample_id'), row.get('target_key'))}:backend_magnitude_probability_missing")
            continue
        if stable.get("magnitude_calibration_state") != "shadow_calibrated_backend":
            reasons.append(f"{member_id(row.get('sample_id'), row.get('target_key'))}:stable_magnitude_calibration_unproven")
            continue
        if candidate.get("magnitude_calibration_state") != "shadow_calibrated_backend":
            reasons.append(f"{member_id(row.get('sample_id'), row.get('target_key'))}:candidate_magnitude_calibration_unproven")
            continue
        prepared.append(
            {
                **row,
                "actual_magnitude_index": MAGNITUDE_CLASSES.index(actual),
                "stable_magnitude_probabilities": stable_probs,
                "candidate_magnitude_probabilities": candidate_probs,
            }
        )
    return prepared, list(dict.fromkeys(reasons))


def _magnitude_metrics(rows: Sequence[Mapping[str, Any]], role: str) -> dict[str, float]:
    if not rows:
        return {
            "ranked_probability_score": math.nan,
            "ordinal_log_loss": math.nan,
            "high_impact_recall": math.nan,
        }
    rps_values: list[float] = []
    log_values: list[float] = []
    high_actual = 0
    high_recalled = 0
    for row in rows:
        probabilities = list(row[f"{role}_magnitude_probabilities"])
        actual = int(row["actual_magnitude_index"])
        squared = 0.0
        ordinal_log = 0.0
        for threshold in range(len(MAGNITUDE_CLASSES) - 1):
            predicted_cumulative = sum(probabilities[: threshold + 1])
            observed_cumulative = 1.0 if actual <= threshold else 0.0
            squared += (predicted_cumulative - observed_cumulative) ** 2
            probability = (
                predicted_cumulative if observed_cumulative else 1.0 - predicted_cumulative
            )
            ordinal_log += -math.log(
                max(direction_gate.EPSILON, min(1.0 - direction_gate.EPSILON, probability))
            )
        divisor = len(MAGNITUDE_CLASSES) - 1
        rps_values.append(squared / divisor)
        log_values.append(ordinal_log / divisor)
        if actual >= MAGNITUDE_CLASSES.index("high"):
            high_actual += 1
            predicted = max(range(len(probabilities)), key=probabilities.__getitem__)
            if predicted >= MAGNITUDE_CLASSES.index("high"):
                high_recalled += 1
    return {
        "ranked_probability_score": sum(rps_values) / len(rps_values),
        "ordinal_log_loss": sum(log_values) / len(log_values),
        "high_impact_recall": high_recalled / high_actual if high_actual else 0.0,
    }


def _bootstrap_magnitude_differences(
    rows: Sequence[Mapping[str, Any]],
    *,
    regime: str,
    iterations: int,
    seed: int,
) -> dict[str, list[float]]:
    modes = ("day_block", "event_cluster") if regime == "material_event" else ("day_block",)
    by_mode: dict[str, dict[str, list[float]]] = {}
    for mode in modes:
        rng = random.Random(seed + sum(ord(character) for character in mode))
        values = {
            "ranked_probability_score": [],
            "ordinal_log_loss": [],
            "high_impact_recall": [],
        }
        for _ in range(iterations):
            indices = direction_gate._group_resample_indices(rows, rng=rng, mode=mode)
            sample = [rows[index] for index in indices]
            stable = _magnitude_metrics(sample, "stable")
            candidate = _magnitude_metrics(sample, "candidate")
            for metric in values:
                values[metric].append(candidate[metric] - stable[metric])
        by_mode[mode] = values
    result: dict[str, list[float]] = {}
    for metric in by_mode["day_block"]:
        day = by_mode["day_block"][metric]
        if regime != "material_event":
            result[metric] = day
            continue
        cluster = by_mode["event_cluster"][metric]
        lower_better = metric in _LOWER_MAGNITUDE_METRICS
        result[metric] = [
            max(day_value, cluster_value) if lower_better else min(day_value, cluster_value)
            for day_value, cluster_value in zip(day, cluster, strict=True)
        ]
    return result


def _magnitude_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    regime: str,
    iterations: int,
) -> dict[str, Any]:
    stable = _magnitude_metrics(rows, "stable")
    candidate = _magnitude_metrics(rows, "candidate")
    bootstraps = _bootstrap_magnitude_differences(
        rows,
        regime=regime,
        iterations=iterations,
        seed=direction_gate.BOOTSTRAP_SEED + sum(ord(character) for character in regime),
    )
    metrics: dict[str, Any] = {}
    for metric in stable:
        lower_better = metric in _LOWER_MAGNITUDE_METRICS
        values = bootstraps[metric]
        metrics[metric] = {
            "stable": stable[metric],
            "candidate": candidate[metric],
            "candidate_minus_stable": candidate[metric] - stable[metric],
            "paired_95_confidence_bound": direction_gate._quantile(
                values, 0.95 if lower_better else 0.05
            ),
            "confidence_bound_side": "upper" if lower_better else "lower",
            "bootstrap_mode": (
                "worse_of_day_block_and_event_cluster"
                if regime == "material_event"
                else "five_trading_day_moving_blocks"
            ),
        }
    return {"complete": True, "row_count": len(rows), "metrics": metrics}


def evaluate_prospective_release_gate(
    rows: Iterable[Mapping[str, Any]],
    *,
    denominator_manifest: Mapping[str, Any],
    bootstrap_iterations: int = direction_gate.BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    """Evaluate V11 evidence without authorizing rollout or a non-zero weight."""

    try:
        denominator = _validated_denominator(denominator_manifest)
        raw_rows = [dict(row) for row in rows]
        observed_ids = sorted(
            member_id(row.get("sample_id"), row.get("target_key")) for row in raw_rows
        )
        if observed_ids != denominator["eligible_member_ids"]:
            raise ProspectiveEvidenceError("denominator_shrinkage_or_eligibility_mismatch")
        normalized: list[dict[str, Any]] = []
        proof_reasons: list[str] = []
        proof_reasons_by_member: dict[str, list[str]] = {}
        for row in raw_rows:
            checked, reasons = _validate_pair(
                row, denominator_digest=denominator["denominator_digest"]
            )
            normalized.append(checked)
            proof_reasons.extend(reasons)
            proof_reasons_by_member[
                member_id(checked.get("sample_id"), checked.get("target_key"))
            ] = reasons
    except ProspectiveEvidenceError as exc:
        return {
            "contract_version": PROSPECTIVE_EVIDENCE_CONTRACT_VERSION,
            "direction_gate_version": direction_gate.STATISTICAL_RELEASE_GATE_VERSION,
            "direction_gate_spec_hash": direction_gate.GATE_SPEC_HASH,
            "result": "invalid_evidence",
            "reason_codes": [str(exc)],
            "formal_weight": 0.0,
            "released": False,
            "regimes": {},
        }

    core = direction_gate.evaluate_statistical_release_gate(
        normalized, bootstrap_iterations=bootstrap_iterations
    )
    if core["result"] == "invalid_evidence":
        return {
            "contract_version": PROSPECTIVE_EVIDENCE_CONTRACT_VERSION,
            "direction_gate": core,
            "result": "invalid_evidence",
            "reason_codes": list(core.get("reason_codes") or ()),
            "formal_weight": 0.0,
            "released": False,
            "regimes": {},
        }

    magnitude_rows, magnitude_reasons = _magnitude_prepared(normalized)
    magnitude_by_identity = {
        member_id(row["sample_id"], row["target_key"]): row for row in magnitude_rows
    }
    regimes: dict[str, Any] = {}
    for regime, details in core["regimes"].items():
        regime_rows = [row for row in normalized if row.get("regime") == regime]
        regime_proof_reasons = [
            reason
            for row in regime_rows
            for reason in proof_reasons_by_member[
                member_id(row.get("sample_id"), row.get("target_key"))
            ]
        ]
        hard_gate_unknown = False
        candidate_hard_counts = {key: 0 for key in HARD_SAFETY_GATES}
        for row in regime_rows:
            candidate = row.get("candidate")
            counts = _hard_gate_counts(candidate if isinstance(candidate, Mapping) else {})
            if counts is None:
                hard_gate_unknown = True
                continue
            for key, value in counts.items():
                candidate_hard_counts[key] += value
        relevant_magnitude = [
            magnitude_by_identity[member_id(row["sample_id"], row["target_key"])]
            for row in regime_rows
            if member_id(row["sample_id"], row["target_key"]) in magnitude_by_identity
        ]
        magnitude_complete = bool(regime_rows) and len(relevant_magnitude) == len(
            regime_rows
        )
        magnitude = (
            _magnitude_report(
                relevant_magnitude,
                regime=regime,
                iterations=bootstrap_iterations,
            )
            if regime_rows and magnitude_complete and details["result"] != "remain_shadow_insufficient_power"
            else {
                "complete": magnitude_complete,
                "row_count": len(relevant_magnitude),
                "metrics": {},
            }
        )
        reasons = list(details.get("reason_codes") or ())
        result = details["result"]
        if any(candidate_hard_counts.values()):
            result = "reject_safety_regression"
            reasons.append("candidate_safety_hard_gate_nonzero")
        elif regime_proof_reasons or hard_gate_unknown or not magnitude_complete:
            result = "remain_shadow_insufficient_power"
            reasons.extend(regime_proof_reasons)
            if hard_gate_unknown:
                reasons.append("candidate_safety_hard_gate_counts_unproven")
            if not magnitude_complete:
                reasons.append("magnitude_calibration_evidence_incomplete")
        elif result == "pass_for_canary":
            result = "predictive_canary"
        regimes[regime] = {
            **details,
            "result": result,
            "reason_codes": list(dict.fromkeys(reasons)),
            "candidate_safety_hard_gate_counts": candidate_hard_counts,
            "magnitude": magnitude,
        }

    precedence = (
        "invalid_evidence",
        "reject_safety_regression",
        "reject_statistical_regression",
        "remain_shadow_insufficient_power",
        "remain_shadow_noninferior_but_not_superior",
        "predictive_canary",
    )
    regime_results = {details["result"] for details in regimes.values()}
    result = next((item for item in precedence if item in regime_results), "invalid_evidence")
    assert result in VALID_DECISIONS
    return {
        "contract_version": PROSPECTIVE_EVIDENCE_CONTRACT_VERSION,
        "direction_gate_version": direction_gate.STATISTICAL_RELEASE_GATE_VERSION,
        "direction_gate_spec_hash": direction_gate.GATE_SPEC_HASH,
        "bootstrap_iterations": bootstrap_iterations,
        "bootstrap_seed": direction_gate.BOOTSTRAP_SEED,
        "denominator_manifest": denominator,
        "denominator_shrinkage": 0,
        "result": result,
        "reason_codes": list(dict.fromkeys(proof_reasons + magnitude_reasons)),
        "formal_weight": 0.0,
        "released": False,
        "regimes": regimes,
    }
