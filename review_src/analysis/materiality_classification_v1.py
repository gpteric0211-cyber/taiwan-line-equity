from __future__ import annotations

"""Deterministic V11 materiality and regime candidates.

The functions consume backend-validated structured predicates. They do not
infer materiality from free-form prose and never release a directional weight.
"""

from collections.abc import Mapping
from typing import Any


MATERIALITY_CLASSIFICATION_CONTRACT_VERSION = "MaterialityClassificationContractV1"
MATERIALITY_LEVELS = {"critical", "high", "medium", "low", "unknown_pending"}
TARGET_RELATIONSHIP_TYPES = {
    "direct_company",
    "parent_subsidiary_group",
    "supply_chain",
    "customer",
    "peer",
    "incidental_mention",
    "unresolved",
}

_CRITICAL_PREDICATES = {
    "target_trading_halt_caused_by_event",
    "bankruptcy_or_dissolution",
    "effective_control_change",
    "official_target_named_sanction_or_export_prohibition",
    "catastrophic_core_operations_prevented",
}
_HIGH_EVENT_CATEGORIES = {
    "earnings",
    "guidance",
    "investor_conference",
    "monthly_revenue",
    "material_financing",
    "dilution",
    "merger_or_control_proposal",
    "major_order",
    "major_capacity_action",
    "major_asset_action",
    "enforcement",
    "litigation",
    "recall",
    "cyber_incident",
    "operational_incident",
    "direct_government_policy",
}
_ROUTINE_EVENT_CATEGORIES = {
    "routine_disclosure",
    "administrative_notice",
    "calendar_notice",
}
_STRONG_INDIRECT_RELATIONSHIPS = {
    "parent_subsidiary_group",
    "supply_chain",
    "customer",
    "peer",
}


def _values(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("fact_predicates must be a list")
    return {str(item).strip() for item in value if str(item).strip()}


def _result(level: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "contract_version": MATERIALITY_CLASSIFICATION_CONTRACT_VERSION,
        "content_materiality": level,
        "reason_codes": reasons,
        "candidate_regime_hint": (
            "material_pending"
            if level in {"critical", "high"}
            else "normal"
            if level in {"medium", "low"}
            else "suppressed_pending"
        ),
        "formal_direction_weight": 0.0,
        "eligible_for_weight": False,
    }


def classify_content_materiality(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Classify verified structured evidence into the approved five levels."""

    if not isinstance(evidence, Mapping):
        raise ValueError("evidence must be an object")
    blockers: list[str] = []
    if str(evidence.get("verification_state") or "") != "verified":
        blockers.append("verification_not_verified")
    if evidence.get("source_coverage_complete") is not True:
        blockers.append("source_coverage_incomplete")
    if str(evidence.get("entity_resolution_state") or "") != "resolved":
        blockers.append("entity_unresolved")
    if evidence.get("revision_complete") is not True:
        blockers.append("revision_incomplete")
    if blockers:
        return _result("unknown_pending", blockers)

    relationship = str(evidence.get("target_relationship_type") or "unresolved")
    if relationship not in TARGET_RELATIONSHIP_TYPES:
        raise ValueError("target_relationship_type is invalid")
    category = str(evidence.get("event_category") or "unknown").strip().casefold()
    predicates = _values(evidence.get("fact_predicates"))
    critical = sorted(predicates & _CRITICAL_PREDICATES)
    if relationship == "direct_company" and critical:
        return _result("critical", [f"critical_predicate:{critical[0]}"])
    if relationship == "direct_company" and (
        category in _HIGH_EVENT_CATEGORIES
        or "material_financial_or_operational_action" in predicates
    ):
        return _result("high", [f"verified_direct_high_category:{category}"])
    if category in _ROUTINE_EVENT_CATEGORIES:
        return _result("low", [f"verified_routine_category:{category}"])
    if relationship == "direct_company":
        return _result("medium", ["verified_direct_event_without_high_predicate"])
    try:
        relevance = float(evidence.get("target_relevance") or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_relevance must be numeric") from exc
    if relationship in _STRONG_INDIRECT_RELATIONSHIPS and relevance >= 0.7:
        return _result("medium", ["verified_strong_indirect_relationship"])
    return _result("low", ["verified_weak_or_incidental_relationship"])


def classify_target_regime(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Select a zero-weight target regime candidate without releasing it."""

    if not isinstance(evidence, Mapping):
        raise ValueError("evidence must be an object")
    content_level = str(evidence.get("content_materiality") or "unknown_pending")
    if content_level not in MATERIALITY_LEVELS:
        raise ValueError("content_materiality is invalid")
    relationship = str(evidence.get("target_relationship_type") or "unresolved")
    if relationship not in TARGET_RELATIONSHIP_TYPES:
        raise ValueError("target_relationship_type is invalid")
    direction = str(evidence.get("target_direction") or "unknown")
    if direction not in {"positive", "negative", "mixed", "neutral", "unknown"}:
        raise ValueError("target_direction is invalid")
    magnitude = str(evidence.get("target_impact_magnitude") or "unknown_pending")
    if magnitude not in {
        "negligible",
        "low",
        "medium",
        "high",
        "extreme",
        "unknown_pending",
    }:
        raise ValueError("target_impact_magnitude is invalid")

    reasons: list[str] = []
    if (
        str(evidence.get("verification_state") or "") != "verified"
        or content_level == "unknown_pending"
        or relationship == "unresolved"
    ):
        regime = "suppressed_pending"
        reasons.append("verification_entity_or_content_pending")
    elif content_level in {"critical", "high"}:
        if (
            relationship == "direct_company"
            and direction in {"positive", "negative", "mixed"}
            and magnitude != "unknown_pending"
            and evidence.get("target_impact_eligible") is True
        ):
            regime = "material_event"
            reasons.append("verified_direct_material_target_candidate")
        else:
            regime = "material_pending"
            reasons.append("material_content_target_direction_or_calibration_pending")
    else:
        regime = "normal"
        reasons.append("verified_nonmaterial_regime_candidate")
    return {
        "contract_version": MATERIALITY_CLASSIFICATION_CONTRACT_VERSION,
        "target_materiality": content_level,
        "target_direction": direction,
        "target_impact_magnitude": magnitude,
        "regime_selection": regime,
        "reason_codes": reasons,
        "candidate_contribution": 0.0,
        "eligible_for_weight": False,
        "released": False,
    }
