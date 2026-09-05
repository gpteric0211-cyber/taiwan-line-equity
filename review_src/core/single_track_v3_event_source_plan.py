from __future__ import annotations

"""Versioned Stage 3 source coverage and bounded Radar query plan."""

import re
from typing import Any, Mapping


EVENT_SOURCE_PLAN_VERSION = "SingleTrackV3EventSourcePlanV3"
EVENT_QUERY_PLAN_VERSION = "SingleTrackV3EventQueryPlanV1"
REQUIRED_EVENT_SCAN_SCOPES = (
    "official_company",
    "taiwan_policy",
    "us_policy_geopolitics",
    "company_industry_news",
    "related_overseas_price_reaction",
    "us_market_taiwan_night",
    "dilution_valuation_risk",
)
_COMPLETE_STATUSES = {"ok", "no_results"}
_FAILURE_PRIORITY = (
    "timeout",
    "rate_limited",
    "offline",
    "invalid_response",
    "source_error",
    "policy_disabled",
    "source_delayed",
    "stale",
    "unavailable",
    "partial",
    "missing",
)


SOURCE_DESCRIPTORS = (
    {
        "source_key": "mops_listed_disclosures",
        "scope_keys": ("official_company", "dilution_valuation_risk"),
        "authority_tier": "canonical_official",
        "query_mode": "once_per_run",
        "implementation_state": "execution_plan_wired_off_by_default",
    },
    {
        "source_key": "mops_otc_disclosures",
        "scope_keys": ("official_company", "dilution_valuation_risk"),
        "authority_tier": "canonical_official",
        "query_mode": "once_per_run",
        "implementation_state": "execution_plan_wired_off_by_default",
    },
    {
        "source_key": "taiwan_government_policy_official",
        "scope_keys": ("taiwan_policy",),
        "authority_tier": "canonical_official",
        "query_mode": "once_per_run",
        "implementation_state": "execution_plan_wired_off_by_default",
    },
    {
        "source_key": "us_government_policy_official",
        "scope_keys": ("us_policy_geopolitics",),
        "authority_tier": "canonical_official",
        "query_mode": "once_per_run",
        "implementation_state": "execution_plan_wired_off_by_default",
    },
    {
        "source_key": "us_federal_reserve_official",
        "scope_keys": ("us_policy_geopolitics",),
        "authority_tier": "canonical_official",
        "query_mode": "once_per_run",
        "implementation_state": "execution_plan_wired_off_by_default",
    },
    {
        "source_key": "us_sanctions_official",
        "scope_keys": ("us_policy_geopolitics",),
        "authority_tier": "canonical_official",
        "query_mode": "once_per_run",
        "implementation_state": "execution_plan_wired_off_by_default",
    },
    {
        "source_key": "us_export_control_official",
        "scope_keys": ("us_policy_geopolitics",),
        "authority_tier": "canonical_official",
        "query_mode": "once_per_run",
        "implementation_state": "execution_plan_wired_off_by_default",
    },
    {
        "source_key": "geopolitical_official_or_licensed",
        "scope_keys": ("us_policy_geopolitics",),
        "authority_tier": "licensed_secondary",
        "query_mode": "once_per_run",
        "implementation_state": "missing",
    },
    {
        "source_key": "controlled_news_metadata",
        "scope_keys": ("company_industry_news",),
        "authority_tier": "news_radar",
        "query_mode": "per_query",
        "implementation_state": "execution_plan_wired_off_by_default",
    },
    {
        "source_key": "related_overseas_price_snapshot",
        "scope_keys": ("related_overseas_price_reaction",),
        "authority_tier": "canonical_normalized_supplemental",
        "query_mode": "sealed_snapshot",
        "implementation_state": "snapshot_receipt_materializer_available_off_by_default",
    },
    {
        "source_key": "us_market_snapshot",
        "scope_keys": ("us_market_taiwan_night",),
        "authority_tier": "canonical_normalized_supplemental",
        "query_mode": "sealed_snapshot",
        "implementation_state": "snapshot_receipt_materializer_available_off_by_default",
    },
    {
        "source_key": "taifex_night_snapshot",
        "scope_keys": ("us_market_taiwan_night",),
        "authority_tier": "canonical_official",
        "query_mode": "sealed_snapshot",
        "implementation_state": "snapshot_receipt_materializer_available_off_by_default",
    },
    {
        "source_key": "dilution_valuation_snapshot",
        "scope_keys": ("dilution_valuation_risk",),
        "authority_tier": "canonical_official",
        "query_mode": "sealed_snapshot",
        "implementation_state": "snapshot_receipt_materializer_available_off_by_default",
    },
)


RETRIEVAL_ATTEMPT_SOURCE_BINDINGS = {
    "mops_listed_disclosures": "mops_listed_disclosures",
    "mops_otc_disclosures": "mops_otc_disclosures",
    "GDELT_DOC_INDEX": "controlled_news_metadata",
    "GOOGLE_NEWS_RSS_INDEX": "controlled_news_metadata",
    "EXECUTIVE_YUAN_NEWS": "taiwan_government_policy_official",
    "EXECUTIVE_YUAN_MINISTRY_NEWS": "taiwan_government_policy_official",
    "MOEA_NEWS": "taiwan_government_policy_official",
    "FEDERAL_RESERVE_MONETARY_POLICY_RSS": "us_federal_reserve_official",
    "US_TREASURY_PRESS_RELEASE_INDEX": "us_government_policy_official",
    "OFAC_RECENT_ACTIONS_INDEX": "us_sanctions_official",
    "BIS_PRESS_RELEASE_INDEX": "us_export_control_official",
}


_SCOPE_REQUIREMENTS = {
    "official_company": {
        "all_of": ("mops_listed_disclosures", "mops_otc_disclosures"),
    },
    "taiwan_policy": {
        "all_of": ("taiwan_government_policy_official",),
    },
    "us_policy_geopolitics": {
        "all_of": (
            "us_government_policy_official",
            "us_federal_reserve_official",
            "us_sanctions_official",
            "us_export_control_official",
            "geopolitical_official_or_licensed",
        ),
    },
    "company_industry_news": {
        "any_of": ("controlled_news_metadata",),
    },
    "related_overseas_price_reaction": {
        "all_of": ("related_overseas_price_snapshot",),
    },
    "us_market_taiwan_night": {
        "all_of": ("us_market_snapshot", "taifex_night_snapshot"),
    },
    "dilution_valuation_risk": {
        "all_of": (
            "mops_listed_disclosures",
            "mops_otc_disclosures",
            "dilution_valuation_snapshot",
        ),
    },
}


def _normalized_status(value: Any) -> str:
    status = str(value or "missing").strip().lower()
    return status if status in {*_COMPLETE_STATUSES, *_FAILURE_PRIORITY} else "source_error"


def _failure_status(statuses: list[str]) -> str:
    for failure in _FAILURE_PRIORITY:
        if failure in statuses:
            return failure
    return "source_error"


def evaluate_event_source_coverage(
    source_statuses: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive the seven scan scopes without silently treating missing sources as neutral."""

    normalized_sources = {
        str(key): _normalized_status(value) for key, value in source_statuses.items()
    }
    scope_statuses: dict[str, str] = {}
    scope_evidence: dict[str, dict[str, Any]] = {}
    for scope in REQUIRED_EVENT_SCAN_SCOPES:
        requirement = _SCOPE_REQUIREMENTS[scope]
        required = tuple(requirement.get("all_of") or requirement.get("any_of") or ())
        observed = {
            source_key: normalized_sources.get(source_key, "missing")
            for source_key in required
        }
        if "all_of" in requirement:
            complete = all(status in _COMPLETE_STATUSES for status in observed.values())
        else:
            complete = any(status in _COMPLETE_STATUSES for status in observed.values())
        scope_statuses[scope] = (
            "ok" if complete else _failure_status(list(observed.values()))
        )
        scope_evidence[scope] = {
            "mode": "all_of" if "all_of" in requirement else "any_of",
            "required_source_keys": list(required),
            "source_statuses": observed,
        }
    incomplete = [
        scope for scope in REQUIRED_EVENT_SCAN_SCOPES if scope_statuses[scope] != "ok"
    ]
    return {
        "source_plan_version": EVENT_SOURCE_PLAN_VERSION,
        "scope_statuses": scope_statuses,
        "scope_evidence": scope_evidence,
        "incomplete_scopes": incomplete,
        "complete": not incomplete,
        "high_confidence_allowed": not incomplete,
    }


def source_statuses_from_retrieval_attempts(
    attempts: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> dict[str, str]:
    """Collapse durable physical attempts into logical source-plan statuses.

    Any failed attempt keeps the logical source incomplete even when a fallback
    succeeds.  This is deliberately conservative: a timeout/429/offline path
    cannot be relabelled as a complete scan merely because another index
    returned candidates.
    """

    grouped: dict[str, list[str]] = {}
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            raise ValueError("retrieval attempt must be an object")
        physical_source = str(attempt.get("source_id") or "").strip()
        logical_source = RETRIEVAL_ATTEMPT_SOURCE_BINDINGS.get(physical_source)
        if logical_source is None:
            continue
        outcome = str(attempt.get("outcome") or "").strip().lower()
        failure_class = str(attempt.get("failure_class") or "").strip().lower()
        source_status = _normalized_status(attempt.get("source_status"))
        if outcome == "failed":
            status = (
                failure_class
                if failure_class in _FAILURE_PRIORITY
                else source_status
                if source_status in _FAILURE_PRIORITY
                else "source_error"
            )
        elif outcome == "no_results" or source_status == "no_results":
            status = "no_results"
        elif outcome == "success" or source_status == "ok":
            status = "ok"
        else:
            status = "source_error"
        grouped.setdefault(logical_source, []).append(status)

    result: dict[str, str] = {}
    for source, statuses in grouped.items():
        failures = [status for status in statuses if status not in _COMPLETE_STATUSES]
        if failures:
            result[source] = _failure_status(failures)
        elif "ok" in statuses:
            result[source] = "ok"
        else:
            result[source] = "no_results"
    return {source: result[source] for source in sorted(result)}


def merge_event_source_statuses(
    *status_maps: Mapping[str, Any],
) -> dict[str, str]:
    """Merge news attempts and sealed snapshot receipts without hiding a failure."""

    grouped: dict[str, list[str]] = {}
    for status_map in status_maps:
        if not isinstance(status_map, Mapping):
            raise ValueError("source status input must be an object")
        for source_key, raw_status in status_map.items():
            key = str(source_key or "").strip()
            if not key:
                raise ValueError("source status key is required")
            grouped.setdefault(key, []).append(_normalized_status(raw_status))
    merged: dict[str, str] = {}
    for source_key, statuses in grouped.items():
        failures = [status for status in statuses if status not in _COMPLETE_STATUSES]
        if failures:
            merged[source_key] = _failure_status(failures)
        elif "ok" in statuses:
            merged[source_key] = "ok"
        else:
            merged[source_key] = "no_results"
    return {source_key: merged[source_key] for source_key in sorted(merged)}


def _terms(value: Any, field: str, *, maximum_items: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    result: list[str] = []
    for raw in value:
        text = re.sub(r"[\x00-\x1f\"]+", " ", str(raw or ""))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if len(text) > 80:
            raise ValueError(f"{field} contains an overlong term")
        if text not in result:
            result.append(text)
    if len(result) > maximum_items:
        raise ValueError(f"{field} exceeds its item limit")
    return result


def build_bounded_event_radar_queries(entity: Mapping[str, Any]) -> dict[str, Any]:
    """Build discovery-only queries from a backend-supplied official entity packet."""

    if not isinstance(entity, Mapping):
        raise ValueError("entity packet must be an object")
    stock_code = str(entity.get("stock_code") or "").strip()
    if not re.fullmatch(r"\d{4}", stock_code):
        raise ValueError("entity.stock_code must be a four-digit official code")
    registry_version = str(entity.get("registry_version") or "").strip()
    if not registry_version:
        raise ValueError("entity.registry_version is required")
    official_names = _terms(entity.get("official_names"), "official_names", maximum_items=4)
    audited_aliases = _terms(entity.get("audited_aliases") or [], "audited_aliases", maximum_items=6)
    industry_terms = _terms(entity.get("industry_terms") or [], "industry_terms", maximum_items=8)
    related_symbols = _terms(entity.get("related_symbols") or [], "related_symbols", maximum_items=8)
    if not official_names:
        raise ValueError("at least one official company name is required")
    company_terms = [stock_code, *official_names, *audited_aliases]

    def joined(values: list[str]) -> str:
        return " OR ".join(f'"{value}"' for value in values)

    queries = [
        f"({joined(company_terms)}) (重大訊息 OR announcement OR earnings OR guidance OR suspension)",
        f"({joined(company_terms)}) (可轉債 OR convertible bond OR 增資 OR dilution OR valuation risk)",
    ]
    if industry_terms:
        queries.append(
            f"({joined(company_terms + industry_terms)}) (export control OR sanctions OR tariff OR supply chain)"
        )
    if related_symbols:
        queries.append(
            f"({joined(related_symbols)}) (price reaction OR earnings OR guidance OR industry outlook)"
        )
    normalized = [re.sub(r"\s+", " ", query).strip() for query in queries]
    if len(normalized) > 4 or any(len(query) > 512 for query in normalized):
        raise ValueError("event radar query plan exceeds its bounded contract")
    return {
        "query_plan_version": EVENT_QUERY_PLAN_VERSION,
        "registry_version": registry_version,
        "stock_code": stock_code,
        "queries": normalized,
        "query_count": len(normalized),
        "policy_relevance_terms": sorted(set(company_terms + industry_terms)),
        "discovery_only": True,
        "can_verify_event": False,
        "can_override_referee": False,
    }
