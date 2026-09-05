from __future__ import annotations

"""Typed, bounded projection contract for the LINE model shadow path.

This module does not query a database, fetch external content, or calculate a
financial indicator.  It only projects the already-approved LINE market facts
into the versioned ModelFactPacketV2 contract.
"""

import math
import re
import copy
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from core.public_url import normalize_public_https_url


MODEL_FACT_PACKET_VERSION = "model-fact-packet-v2"
TOKEN_ESTIMATOR_VERSION = "charclass-conservative-v2"
MODEL_OUTPUT_RULES_VERSION = "model-analysis-output-rules-v1"
DEFAULT_RESERVED_OUTPUT_TOKENS = 900
_ISO_DATE_TEXT = re.compile(r"20\d{2}-\d{2}-\d{2}")


@dataclass(frozen=True)
class ContextProfile:
    name: str
    prompt_token_cap: int
    facts_total: int
    facts_per_scope: int
    events_total: int
    events_per_scope: int
    minimum_context: int
    release_state: str


CONTEXT_PROFILES: dict[str, ContextProfile] = {
    "focused-16k-v1": ContextProfile(
        name="focused-16k-v1",
        prompt_token_cap=6_000,
        facts_total=48,
        facts_per_scope=16,
        events_total=4,
        events_per_scope=2,
        minimum_context=16_384,
        release_state="stable",
    ),
    "comprehensive-16k-v1": ContextProfile(
        name="comprehensive-16k-v1",
        prompt_token_cap=11_000,
        facts_total=96,
        facts_per_scope=24,
        events_total=8,
        events_per_scope=3,
        minimum_context=16_384,
        release_state="stable",
    ),
    "comprehensive-32k-candidate-v1": ContextProfile(
        name="comprehensive-32k-candidate-v1",
        prompt_token_cap=20_000,
        facts_total=160,
        facts_per_scope=32,
        events_total=12,
        events_per_scope=4,
        minimum_context=32_768,
        release_state="candidate",
    ),
}


@dataclass(frozen=True)
class ProfileSelection:
    profile: ContextProfile
    requested_profile: str | None
    fallback_reason: str | None


@dataclass(frozen=True)
class PacketBuildResult:
    packet: dict[str, Any]
    dropped_facts: int
    dropped_events: int


@dataclass(frozen=True)
class TokenPreflight:
    estimator: str
    estimated_prompt_tokens: int
    actual_context: int
    reserved_output_tokens: int
    safety_margin: int
    context_prompt_capacity: int
    effective_prompt_budget: int
    ready: bool
    reason: str


@dataclass(frozen=True)
class PacketCompactionResult:
    packet: dict[str, Any]
    original_preflight: TokenPreflight
    compacted_preflight: TokenPreflight
    removed_fact_ids: tuple[str, ...]
    removed_event_ids: tuple[str, ...]


_STABLE_PROFILE_FOR_DEPTH = {
    "focused": "focused-16k-v1",
    "comprehensive": "comprehensive-16k-v1",
}

_EVENT_SECTIONS = (
    "official_event_context",
    "external_event_context",
    "news_radar_context",
)

_FACT_DOMAIN_SCOPES = {
    "official_ohlcv": "price",
    "intraday_quote": "price",
    "technical": "technical",
    "support_resistance": "support_resistance",
    "valuation": "valuation",
    "price_volume": "volume",
    "institutional_context": "institutional",
    "global_market_context": "global_market",
    "taifex_night_context": "night_market",
    "trading_state": "risk",
    "recommendation_safety": "risk",
    "decision_audit": "risk",
    "evidence_summary": "rationale",
    "advisory": "decision",
}


def evidence_scope_for_domain(domain: str) -> str:
    """Return the request scope represented by a canonical fact domain."""

    return _FACT_DOMAIN_SCOPES.get(str(domain), str(domain))


_REQUEST_SCOPE_ALIASES = {
    "fundamentals": {"valuation", "fundamentals"},
    "current_news": {"events", "current_news"},
    "geopolitics": {"events", "global_market", "geopolitics"},
    "risk": {"risk", "rationale", "decision"},
    "decision": {"decision", "rationale", "risk"},
}


def fact_supports_requested_scope(fact: dict[str, Any], requested_scope: str) -> bool:
    """Return whether one packet fact is eligible to cover a requested scope."""

    represented_scope = evidence_scope_for_domain(str(fact.get("domain") or ""))
    scope = str(requested_scope or "")
    return represented_scope in _REQUEST_SCOPE_ALIASES.get(scope, {scope})

_FOCUS_SCOPES = {
    "overview": (
        "price",
        "technical",
        "valuation",
        "institutional",
        "events",
        "risk",
    ),
    "technical_decision": ("technical", "decision", "risk"),
    "support_resistance": ("support_resistance", "risk"),
    "fundamentals": ("fundamentals",),
    "chips": ("institutional",),
    "news": ("events", "risk"),
    "global_market": ("global_market",),
    "night_market": ("night_market",),
    "history": ("history",),
}

_STRUCTURAL_KEYS = {
    "available",
    "decision_ready",
    "display_only",
    "official_trusted",
    "status",
    "quality",
    "reason",
    "reason_code",
    "availability_reason",
    "can_override_main_status",
    "can_be_overridden_by_model",
    "ready_for_referee",
}

_PROTECTED_CORPORATE_ACTION_FIELDS = (
    "action_date",
    "action_type",
    "adjustment_method",
    "stock_distribution_ratio",
    "ratio_unit",
    "cash_dividend_per_share",
    "share_count_factor",
    "pre_event_price_multiplier",
    "verification_status",
    "source_id",
)

_DISALLOWED_KEYS = {
    "answer_contract",
    "conversation",
    "verified_claims",
    "display",
    "code",
    "stock",
    "referee",
    "provider",
    "source",
    "sources",
    "db_path",
    "token",
    "user_id",
    "reply_token",
}

_CANONICAL_QUALITY = {
    "ok",
    "estimated",
    "stale",
    "source_delayed",
    "missing",
    "unavailable",
}

_NUMERIC_TEXT = re.compile(r"^-?(?:\d+(?:\.\d+)?|\.\d+)$")


def select_context_profile(
    depth: str,
    actual_context: int,
    *,
    requested_profile: str | None = None,
) -> ProfileSelection:
    """Select a stable profile unless an explicit eligible candidate is requested."""

    normalized_depth = "comprehensive" if depth == "comprehensive" else "focused"
    stable = CONTEXT_PROFILES[_STABLE_PROFILE_FOR_DEPTH[normalized_depth]]
    requested = str(requested_profile or "").strip() or None
    if requested is None:
        return ProfileSelection(stable, None, None)
    candidate = CONTEXT_PROFILES.get(requested)
    if candidate is None:
        return ProfileSelection(stable, requested, "unknown_profile")
    if candidate.release_state == "candidate" and actual_context < candidate.minimum_context:
        return ProfileSelection(stable, requested, "candidate_context_not_available")
    if candidate.release_state == "candidate" and normalized_depth != "comprehensive":
        return ProfileSelection(stable, requested, "candidate_requires_comprehensive_depth")
    if actual_context < candidate.minimum_context:
        return ProfileSelection(stable, requested, "profile_context_not_available")
    return ProfileSelection(candidate, requested, None)


def scopes_for_focus(focus: str) -> list[str]:
    normalized = str(focus or "overview").strip() or "overview"
    return list(_FOCUS_SCOPES.get(normalized, (normalized,)))


def _domain_requested(domain: str, scopes: set[str]) -> bool:
    if domain in {"official_ohlcv", "trading_state", "recommendation_safety", "decision_audit"}:
        return True
    scope = _FACT_DOMAIN_SCOPES[domain]
    if scope in scopes:
        return True
    aliases = {
        "technical": {"support_resistance", "decision"},
        "valuation": {"fundamentals"},
        "global_market": {"geopolitics"},
        "rationale": {"risk", "decision"},
        "decision": {"risk"},
    }
    return bool(scopes.intersection(aliases.get(scope, set())))


def _projection_quality(section: Any) -> tuple[str, str | None]:
    """Carry canonical quality when present; otherwise restrict use conservatively."""

    if not isinstance(section, dict):
        return "unavailable", "source_not_supported"
    explicit = str(section.get("quality") or section.get("status") or "").strip().lower()
    if explicit in _CANONICAL_QUALITY:
        quality = explicit
    elif section.get("available") is False:
        quality = "unavailable"
    elif section.get("official_trusted") is False:
        quality = "unavailable"
    elif explicit in {"current", "ready", "official", "high"}:
        quality = "ok"
    elif section.get("available") is True or section.get("decision_ready") is True:
        quality = "ok"
    else:
        quality = "missing"
    reason = str(section.get("availability_reason") or "").strip() or None
    if reason is None and quality in {"missing", "unavailable"}:
        reason = "source_not_supported"
    return quality, reason


def _period_for_domain(domain: str) -> str:
    if domain == "intraday_quote":
        return "intraday"
    if domain in {"official_event_context", "external_event_context", "news_radar_context"}:
        return "event"
    return "daily"


def _unit_for_field(field: str, *, domain: str = "") -> tuple[str | None, str | None]:
    full_key = field.lower()
    key = full_key.split(".")[-1].replace("[", "_")
    if domain == "technical":
        if "volume" in full_key or key == "obv":
            return "shares", None
        if any(
            marker in full_key
            for marker in ("moving_averages.", "bollinger.", "atr", "macd.")
        ):
            return "TWD", "TWD"
    if key.endswith("pct") or "percent" in key or "yield_pct" in key:
        return "percent", None
    if key.startswith("rsi") or key in {"k", "d", "impact_score", "score"}:
        return "index", None
    if (
        any(term in key for term in ("pe_ratio", "pb_ratio", "ratio", "multiple"))
        or key in {"share_count_factor", "pre_event_price_multiplier"}
    ):
        return "ratio", None
    if "volume_shares" in key or key.endswith("shares") or key.endswith("_shares"):
        return "shares", None
    if key.endswith("lots") or key.endswith("_lots"):
        return "lots", None
    if key.endswith("days") or key.endswith("_days") or key.endswith("count"):
        return "count", None
    if any(
        term in key
        for term in (
            "price",
            "open",
            "high",
            "low",
            "close",
            "ma5",
            "ma10",
            "ma20",
            "ma60",
            "support",
            "resistance",
            "turnover_twd",
            "capital_twd",
            "market_cap_twd",
            "cost",
        )
    ):
        return "TWD", "TWD"
    return None, None


def _is_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return bool(_NUMERIC_TEXT.fullmatch(str(value).strip()))


def _is_iso_date(value: Any) -> bool:
    return isinstance(value, str) and bool(_ISO_DATE_TEXT.fullmatch(value.strip()))


def _iter_scalar_leaves(value: Any, *, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key in sorted(value):
            key_text = str(key)
            if key_text in _STRUCTURAL_KEYS or key_text in _DISALLOWED_KEYS:
                continue
            child = f"{prefix}.{key_text}" if prefix else key_text
            yield from _iter_scalar_leaves(value[key], prefix=child)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]"
            yield from _iter_scalar_leaves(item, prefix=child)
        return
    if value is None or isinstance(value, (str, int, float, bool)):
        yield prefix, value


def _immutable_referee(value: Any) -> dict[str, Any]:
    referee = value if isinstance(value, dict) else {}
    return {
        "version": str(referee.get("version") or ""),
        "main_status": str(referee.get("main_status") or "資料不足"),
        "reasons": [str(item) for item in list(referee.get("main_reasons") or [])[:5]],
        "decision_ready": referee.get("decision_ready") is True,
        "immutable": True,
    }


def _event_rows(section: Any) -> list[dict[str, Any]]:
    if not isinstance(section, dict):
        return []
    rows = section.get("events")
    return [dict(item) for item in rows or [] if isinstance(item, dict)]


def _event_verification(section_name: str, event: dict[str, Any]) -> str:
    explicit = str(event.get("verification_state") or "").strip()
    allowed = {
        "discovered",
        "unverified",
        "primary_verified",
        "secondary_corroborated",
        "contradicted",
        "pending_reconciliation",
        "expired",
    }
    if explicit in allowed:
        return explicit
    if section_name == "official_event_context":
        return "primary_verified"
    return "unverified"


def build_model_fact_packet_v2(
    model_facts: dict[str, Any],
    *,
    focus: str,
    depth: str,
    profile: ContextProfile,
    requested_scopes: list[str] | tuple[str, ...] | None = None,
) -> PacketBuildResult:
    """Build a deterministic, count-bounded packet from an approved public projection."""

    display = model_facts.get("display")
    source = display if isinstance(display, dict) else model_facts
    trade_date = str(model_facts.get("trade_date") or source.get("trade_date") or "")
    analysis_cutoff = str(
        model_facts.get("analysis_cutoff")
        or source.get("analysis_cutoff")
        or trade_date
    )
    scopes = list(dict.fromkeys(requested_scopes or scopes_for_focus(focus)))
    requested_scope_set = set(scopes)
    facts: list[dict[str, Any]] = []
    facts_per_scope: dict[str, int] = {}
    dropped_facts = 0
    included_sections: list[str] = []
    omitted_sections: list[str] = []
    omission_reasons: dict[str, str] = {}
    reserved_corporate_action_fields: set[str] = set()

    safety = source.get("recommendation_safety")
    action = (
        safety.get("corporate_action")
        if isinstance(safety, dict)
        and isinstance(safety.get("corporate_action"), dict)
        else {}
    )
    if (
        _domain_requested("recommendation_safety", requested_scope_set)
        and action.get("status") == "active_window"
        and action.get("confirmed") is True
        and action.get("verification_status") == "official_verified"
    ):
        for action_field in _PROTECTED_CORPORATE_ACTION_FIELDS:
            value = action.get(action_field)
            if value is None or len(facts) >= profile.facts_total:
                continue
            field = f"corporate_action.{action_field}"
            unit, currency = _unit_for_field(
                field,
                domain="recommendation_safety",
            )
            numeric = _is_numeric(value)
            dated = _is_iso_date(value)
            if numeric and unit is None:
                unit = "count"
            use_scope = ["explanation"]
            if numeric:
                use_scope.append("numeric_claim")
            if dated:
                use_scope.append("date_claim")
            facts.append(
                {
                    "fact_id": f"F{len(facts) + 1:03d}",
                    "domain": "recommendation_safety",
                    "field": field,
                    "value": value,
                    "unit": unit,
                    "currency": currency,
                    "period": "corporate_action",
                    "trade_date": trade_date,
                    "as_of": analysis_cutoff,
                    "authority_tier": "canonical_db",
                    "quality": "ok",
                    "availability_reason": None,
                    "use_scope": use_scope,
                }
            )
            facts_per_scope["risk"] = facts_per_scope.get("risk", 0) + 1
            reserved_corporate_action_fields.add(field)
        if reserved_corporate_action_fields:
            included_sections.append("recommendation_safety")

    for domain, scope in _FACT_DOMAIN_SCOPES.items():
        if not _domain_requested(domain, requested_scope_set):
            continue
        section = source.get(domain)
        if section is None:
            continue
        quality, availability_reason = _projection_quality(section)
        section_added = False
        for field, value in _iter_scalar_leaves(section):
            if not field:
                continue
            if (
                domain == "recommendation_safety"
                and field in reserved_corporate_action_fields
            ):
                continue
            if len(facts) >= profile.facts_total or facts_per_scope.get(scope, 0) >= profile.facts_per_scope:
                dropped_facts += 1
                omitted_sections.append(domain)
                omission_reasons[domain] = "profile_fact_count_limit"
                continue
            unit, currency = _unit_for_field(field, domain=domain)
            numeric = _is_numeric(value)
            dated = _is_iso_date(value)
            if numeric and unit is None:
                unit = "count"
            use_scope = ["explanation"]
            if numeric and quality in {"ok", "estimated"}:
                use_scope.append("numeric_claim")
            if dated and quality in {"ok", "estimated"}:
                use_scope.append("date_claim")
            if quality in {"missing", "unavailable", "stale", "source_delayed"}:
                use_scope = ["limitation"]
            fact = {
                "fact_id": f"F{len(facts) + 1:03d}",
                "domain": domain,
                "field": field,
                "value": value,
                "unit": unit,
                "currency": currency,
                "period": _period_for_domain(domain),
                "trade_date": trade_date,
                "as_of": trade_date,
                "authority_tier": "canonical_db",
                "quality": quality,
                "availability_reason": availability_reason,
                "use_scope": use_scope,
            }
            facts.append(fact)
            facts_per_scope[scope] = facts_per_scope.get(scope, 0) + 1
            section_added = True
        if section_added:
            included_sections.append(domain)

    events: list[dict[str, Any]] = []
    events_per_scope: dict[str, int] = {}
    dropped_events = 0
    event_requested = bool(requested_scope_set.intersection({"events", "current_news", "geopolitics"}))
    if "current_news" in requested_scope_set:
        event_sections = (
            "news_radar_context",
            "official_event_context",
            "external_event_context",
        )
    elif "geopolitics" in requested_scope_set:
        event_sections = (
            "external_event_context",
            "news_radar_context",
            "official_event_context",
        )
    else:
        event_sections = _EVENT_SECTIONS
    for section_name in event_sections if event_requested else ():
        rows = _event_rows(source.get(section_name))
        for event in rows:
            event_scope = section_name
            if len(events) >= profile.events_total or events_per_scope.get(event_scope, 0) >= profile.events_per_scope:
                dropped_events += 1
                omitted_sections.append(section_name)
                omission_reasons[section_name] = "profile_event_count_limit"
                continue
            rights = event.get("rights") if isinstance(event.get("rights"), dict) else {}
            citation_url = normalize_public_https_url(
                event.get("source_url") or event.get("url")
            )
            index_seen_at = str(
                event.get("index_seen_at") or event.get("seendate") or ""
            )
            publisher_published_at = event.get("publisher_published_at")
            if publisher_published_at in (None, "") and not index_seen_at:
                publisher_published_at = event.get("published_at") or event.get("event_date")
            events.append(
                {
                    "event_id": f"E{len(events) + 1:03d}",
                    "event_context": section_name,
                    "title": str(event.get("title") or "")[:160],
                    "publisher": str(event.get("publisher") or ""),
                    "publisher_published_at": publisher_published_at,
                    "index_seen_at": index_seen_at,
                    "retrieved_at": str(event.get("retrieved_at") or ""),
                    "verification_state": _event_verification(section_name, event),
                    "untrusted_text": True,
                    "source_url": citation_url,
                    "allow_display": bool(citation_url and rights.get("allow_display") is True),
                    "citation_required": bool(event.get("citation_required")),
                    "attribution_required": bool(rights.get("attribution_required")),
                    "source_policy_version": str(rights.get("policy_version") or ""),
                }
            )
            events_per_scope[event_scope] = events_per_scope.get(event_scope, 0) + 1
            if section_name not in included_sections:
                included_sections.append(section_name)

    covered_scopes = {
        scope for domain, scope in _FACT_DOMAIN_SCOPES.items() if domain in included_sections
    }
    if events:
        covered_scopes.update({"events", "current_news"})
        if "geopolitics" in requested_scope_set:
            covered_scopes.add("geopolitics")
    if "valuation" in covered_scopes:
        covered_scopes.add("fundamentals")
    for scope in scopes:
        if scope in covered_scopes or len(facts) >= profile.facts_total:
            continue
        facts.append(
            {
                "fact_id": f"F{len(facts) + 1:03d}",
                "domain": scope,
                "field": "availability",
                "value": None,
                "unit": None,
                "currency": None,
                "period": "current",
                "trade_date": trade_date,
                "as_of": trade_date,
                "authority_tier": "canonical_db",
                "quality": "unavailable",
                "availability_reason": "source_not_supported",
                "use_scope": ["limitation"],
            }
        )
        omitted_sections.append(scope)
        omission_reasons[scope] = "no_eligible_canonical_facts"

    included_unique = sorted(set(included_sections))
    omitted_unique = sorted(set(omitted_sections))
    partial_unique = sorted(set(included_unique).intersection(omitted_unique))
    fully_omitted = sorted(set(omitted_unique).difference(included_unique))
    packet = {
        "contract_version": MODEL_FACT_PACKET_VERSION,
        "request": {
            "depth": "comprehensive" if depth == "comprehensive" else "focused",
            "scopes": scopes,
            "analysis_cutoff": analysis_cutoff,
            "locale": "zh-TW",
        },
        "referee": _immutable_referee(model_facts.get("referee") or source.get("referee")),
        "facts": facts,
        "events": events,
        "conflicts": [],
        "coverage": {
            "included_sections": included_unique,
            "partially_omitted_sections": partial_unique,
            "omitted_sections": fully_omitted,
            "omission_reasons": dict(sorted(omission_reasons.items())),
        },
    }
    _refresh_render_contract(packet)
    return PacketBuildResult(packet=packet, dropped_facts=dropped_facts, dropped_events=dropped_events)


def _iso_day(value: Any) -> date | None:
    if not isinstance(value, str) or not _ISO_DATE_TEXT.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _valid_cost_sample_limitation(packet: dict[str, Any], facts: list[dict[str, Any]]) -> bool:
    request = packet.get("request") if isinstance(packet.get("request"), dict) else {}
    if "institutional" not in (request.get("scopes") or []):
        return False
    cutoff = _iso_day(request.get("analysis_cutoff"))
    prefix = "canonical_costs.foreign_estimated."
    required = [fact for fact in facts if fact.get("field") == prefix + "required_days"]
    sample = [fact for fact in facts if fact.get("field") == prefix + "sample_days"]
    if cutoff is None or len(required) != 1 or len(sample) != 1:
        return False
    values: list[Decimal] = []
    for fact in (required[0], sample[0]):
        fact_id = fact.get("fact_id")
        scopes = fact.get("use_scope")
        if (
            fact.get("domain") != "institutional_context"
            or fact.get("authority_tier") != "canonical_db"
            or fact.get("quality") != "ok"
            or fact.get("unit") != "count"
            or fact.get("period") != "daily"
            or not isinstance(scopes, list)
            or not {"numeric_claim", "explanation"}.issubset(scopes)
            or not isinstance(fact_id, str)
            or not fact_id
            or sum(other.get("fact_id") == fact_id for other in facts) != 1
            or isinstance(fact.get("value"), bool)
        ):
            return False
        try:
            value = Decimal(str(fact.get("value")))
        except (InvalidOperation, ValueError):
            return False
        if not value.is_finite() or value != value.to_integral_value():
            return False
        values.append(value)
    for key in ("trade_date", "as_of"):
        observed = _iso_day(required[0].get(key))
        if observed is None or observed > cutoff or required[0].get(key) != sample[0].get(key):
            return False
    required_days, sample_days = values
    return 0 <= sample_days < required_days


def _valuation_baseline_fact_id(packet: dict[str, Any], facts: list[dict[str, Any]]) -> str | None:
    request = packet.get("request") if isinstance(packet.get("request"), dict) else {}
    if "valuation" not in (request.get("scopes") or []):
        return None
    cutoff = _iso_day(request.get("analysis_cutoff"))
    matches = [fact for fact in facts if fact.get("field") == "relative_value_assessment"]
    if cutoff is None or len(matches) != 1:
        return None
    fact = matches[0]
    fact_id = fact.get("fact_id")
    scopes = fact.get("use_scope")
    if (
        fact.get("domain") != "valuation"
        or fact.get("value") != "unavailable_without_peer_or_historical_baseline"
        or fact.get("authority_tier") != "canonical_db"
        or fact.get("quality") != "ok"
        or fact.get("period") != "daily"
        or fact.get("unit") is not None
        or not isinstance(scopes, list)
        or "explanation" not in scopes
        or not isinstance(fact_id, str)
        or not re.fullmatch(r"F[0-9]{3,}", fact_id)
        or sum(other.get("fact_id") == fact_id for other in facts) != 1
    ):
        return None
    for key in ("trade_date", "as_of"):
        observed = _iso_day(fact.get(key))
        if observed is None or observed > cutoff:
            return None
    return fact_id


def _output_rules(
    packet: dict[str, Any],
    facts: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    requested_scopes = [
        str(item) for item in (packet.get("request") or {}).get("scopes") or [] if str(item)
    ]
    topic_groups: dict[tuple[str, str], list[str]] = {}
    generic_groups: dict[str, list[str]] = {}
    for fact in facts:
        fact_id = str(fact.get("fact_id") or "")
        scopes = {str(item) for item in fact.get("use_scope") or []}
        restricted = scopes == {"limitation"} or fact.get("quality") in {
            "missing", "unavailable", "stale", "source_delayed",
        }
        if not fact_id or not restricted or not any(
            fact_supports_requested_scope(fact, scope) for scope in requested_scopes
        ):
            continue
        domain = str(fact.get("domain") or "")
        field = str(fact.get("field") or "")
        if domain == "fundamentals" or field.startswith("fundamentals."):
            topic_groups.setdefault(("基本面資料不可用", domain), []).append(fact_id)
        elif domain == "support_resistance" or field.startswith("support_resistance."):
            topic_groups.setdefault(("支撐壓力區間資料不可用", domain), []).append(fact_id)
        else:
            generic_groups.setdefault(domain or "unknown", []).append(fact_id)

    limitation_blocks: list[dict[str, Any]] = []
    missing_data: list[str] = []
    for (text, _domain), ids in topic_groups.items():
        limitation_blocks.append({"text_template": text, "evidence_ids": list(dict.fromkeys(ids))[:8]})
        missing_data.append(text)

    valuation_id = _valuation_baseline_fact_id(packet, facts)
    if valuation_id:
        limitation_blocks.append({
            "text_template": "缺少同業或歷史估值基準。無法進行相對估值判斷",
            "evidence_ids": [valuation_id],
        })
        missing_data.append("缺少同業或歷史估值基準")

    for ids in generic_groups.values():
        limitation_blocks.append({
            "text_template": "資料不可用",
            "evidence_ids": list(dict.fromkeys(ids))[:8],
        })

    unverified_event_ids = [
        str(event.get("event_id") or "")
        for event in events
        if event.get("event_id") and event.get("verification_state") == "unverified"
    ]
    if events and len(unverified_event_ids) == len(events):
        missing_data.append("事件尚未驗證")
    if generic_groups:
        missing_data.append("資料不可用")
    if unverified_event_ids:
        limitation_blocks.append({
            "text_template": "事件尚未驗證",
            "evidence_ids": list(dict.fromkeys(unverified_event_ids))[:8],
        })

    research_limitations = (
        ["本包外資近期增量成本估算樣本不足"]
        if _valid_cost_sample_limitation(packet, facts)
        else []
    )
    return {
        "version": MODEL_OUTPUT_RULES_VERSION,
        "same_block_placeholders": True,
        "comparison_pair_required": True,
        "used_event_ids": "exactly_cited_event_ids",
        "missing_data_exact": list(dict.fromkeys(missing_data))[:8],
        "research_limitations_exact": research_limitations,
        "limitation_blocks_exact": limitation_blocks[:8],
        "unlisted_limitation_text": "forbidden",
    }


def _refresh_render_contract(packet: dict[str, Any]) -> None:
    facts = [item for item in packet.get("facts") or [] if isinstance(item, dict)]
    eligible: list[str] = []
    limitation_only: list[str] = []
    for fact in facts:
        fact_id = str(fact.get("fact_id") or "")
        use_scope = {str(item) for item in fact.get("use_scope") or []}
        if (
            fact_id
            and fact.get("authority_tier") == "canonical_db"
            and fact.get("quality") in {"ok", "estimated"}
            and {"numeric_claim", "date_claim"}.intersection(use_scope)
        ):
            eligible.append(fact_id)
        elif fact_id and (
            use_scope == {"limitation"}
            or fact.get("quality") in {"missing", "unavailable", "stale", "source_delayed"}
        ):
            limitation_only.append(fact_id)
    requested_scopes = [
        str(item)
        for item in (packet.get("request") or {}).get("scopes") or []
        if str(item)
    ]
    events = [item for item in packet.get("events") or [] if isinstance(item, dict)]
    scope_evidence_ids: dict[str, list[str]] = {}
    for scope in requested_scopes:
        fact_ids = [
            str(fact.get("fact_id") or "")
            for fact in facts
            if fact.get("fact_id") and fact_supports_requested_scope(fact, scope)
        ]
        event_ids = (
            [
                str(event.get("event_id") or "")
                for event in events
                if event.get("event_id")
                and (
                    scope != "current_news"
                    or event.get("event_context") == "news_radar_context"
                )
            ]
            if scope in {"current_news", "events", "geopolitics"}
            else []
        )
        if scope == "current_news" and not event_ids:
            event_ids = [
                str(event.get("event_id") or "")
                for event in events
                if event.get("event_id")
            ]
        scope_evidence_ids[scope] = list(dict.fromkeys([*fact_ids, *event_ids]))[:8]
    packet["render_contract"] = {
        "placeholder_eligible_fact_ids": sorted(eligible),
        "limitation_only_fact_ids": sorted(limitation_only),
        "event_evidence_ids": sorted(
            str(item.get("event_id") or "")
            for item in packet.get("events") or []
            if isinstance(item, dict) and item.get("event_id")
        ),
        "relative_valuation_claim_allowed": any(
            fact.get("quality") in {"ok", "estimated"}
            and any(
                marker in str(fact.get("field") or "").lower()
                for marker in (
                    "valuation_percentile",
                    "peer_valuation",
                    "relative_value_assessment",
                    "historical_valuation",
                )
            )
            and str(fact.get("value") or "")
            and not str(fact.get("value") or "").lower().startswith("unavailable")
            for fact in facts
        ),
        "scope_evidence_ids": scope_evidence_ids,
        "output_rules": _output_rules(packet, facts, events),
    }


def finalize_model_fact_packet_v2(packet: dict[str, Any]) -> dict[str, Any]:
    """Attach the shared validator/render contract to an already normalized V2 packet."""

    if str(packet.get("contract_version") or "") != MODEL_FACT_PACKET_VERSION:
        raise ValueError("packet contract_version must be model-fact-packet-v2")
    _refresh_render_contract(packet)
    return packet


def conservative_prompt_token_estimate(*parts: str, template_overhead: int = 128) -> int:
    """Return a tested conservative estimate for Qwen JSON/CJK prompts.

    ASCII JSON is budgeted at one token per two characters and non-ASCII text
    at one and a half tokens per character. This remains above observed Ollama
    prompt counts while avoiding the three-token penalty that raw UTF-8 byte
    counting applies to every common CJK character.
    """

    estimate = max(0, int(template_overhead))
    for part in parts:
        text = str(part)
        ascii_chars = sum(1 for character in text if ord(character) < 128)
        non_ascii_chars = len(text) - ascii_chars
        estimate += math.ceil((ascii_chars / 2) + (non_ascii_chars * 1.5))
    return estimate


def _prompt_estimate(system_prompt: str, question: str, packet: dict[str, Any]) -> int:
    packet_json = json.dumps(packet, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    user_prompt = f"使用者問題：{question}\nMODEL_FACT_PACKET_V2：{packet_json}"
    return conservative_prompt_token_estimate(system_prompt, user_prompt)


def compact_packet_to_token_budget(
    packet: dict[str, Any],
    *,
    system_prompt: str,
    question: str,
    profile: ContextProfile,
    actual_context: int,
    reserved_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS,
) -> PacketCompactionResult:
    """Deterministically trim low-priority evidence until the preflight fits."""

    compacted = copy.deepcopy(packet)
    original = preflight_prompt_tokens(
        _prompt_estimate(system_prompt, question, compacted),
        profile=profile,
        actual_context=actual_context,
        reserved_output_tokens=reserved_output_tokens,
    )
    current = original
    removed_facts: list[str] = []
    removed_events: list[str] = []
    coverage = compacted.setdefault("coverage", {})
    omitted = coverage.setdefault("omitted_sections", [])
    reasons = coverage.setdefault("omission_reasons", {})

    def refresh() -> TokenPreflight:
        _refresh_render_contract(compacted)
        return preflight_prompt_tokens(
            _prompt_estimate(system_prompt, question, compacted),
            profile=profile,
            actual_context=actual_context,
            reserved_output_tokens=reserved_output_tokens,
        )

    domain_priority = {
        "advisory": 0,
        "evidence_summary": 1,
        "decision_audit": 2,
        "global_market_context": 3,
        "taifex_night_context": 4,
        "institutional_context": 5,
        "price_volume": 6,
        "valuation": 7,
        "technical": 8,
        "official_ohlcv": 9,
    }
    requested_scopes = set(
        str(item) for item in (compacted.get("request") or {}).get("scopes") or []
    )
    requested_domain_minimums = {
        "official_ohlcv": 5,
        "trading_state": 1,
        "technical": 6 if requested_scopes.intersection(
            {"technical", "support_resistance", "decision", "risk"}
        ) else 1,
        "support_resistance": 1,
        "valuation": 4 if requested_scopes.intersection({"valuation", "fundamentals"}) else 1,
        "institutional_context": 6 if "institutional" in requested_scopes else 1,
        "global_market_context": 3 if requested_scopes.intersection(
            {"global_market", "geopolitics"}
        ) else 1,
        "taifex_night_context": 3 if "night_market" in requested_scopes else 1,
    }

    def removal_value_priority(item: dict[str, Any]) -> int:
        field = str(item.get("field") or "").lower()
        if any(
            marker in field
            for marker in (
                "contract_version",
                "formula_version",
                "metric_id",
                "label",
                "cost_type",
                "cost_label",
                "score_weight",
                "eligible",
            )
        ):
            return 0
        if item.get("use_scope") == ["explanation"]:
            return 1
        if any(
            marker in field
            for marker in (
                "estimated_cost",
                ".value",
                "confidence",
                "quality_state",
                "sample_days",
                "required_days",
                "pe_ratio",
                "pb_ratio",
                "dividend_yield",
                "stance",
            )
        ):
            return 3
        return 2

    def protected_from_compaction(item: dict[str, Any]) -> bool:
        return bool(
            str(item.get("domain") or "") == "recommendation_safety"
            and str(item.get("field") or "").removeprefix("corporate_action.")
            in _PROTECTED_CORPORATE_ACTION_FIELDS
            and item.get("quality") == "ok"
        )

    facts = list(compacted.get("facts") or [])
    domain_counts: dict[str, int] = {}
    for fact in facts:
        domain = str(fact.get("domain") or "")
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    candidates = sorted(
        facts,
        key=lambda item: (
            removal_value_priority(item),
            domain_priority.get(str(item.get("domain") or ""), 4),
            str(item.get("fact_id") or ""),
        ),
    )
    for fact in candidates:
        if current.ready:
            break
        if protected_from_compaction(fact):
            continue
        domain = str(fact.get("domain") or "")
        minimum_count = requested_domain_minimums.get(domain, 1)
        if domain_counts.get(domain, 0) <= minimum_count:
            continue
        fact_id = str(fact.get("fact_id") or "")
        compacted["facts"] = [
            item for item in compacted.get("facts") or [] if str(item.get("fact_id") or "") != fact_id
        ]
        domain_counts[domain] -= 1
        removed_facts.append(fact_id)
        omitted.append(domain)
        reasons[domain] = "token_budget"
        current = refresh()

    # Explicit news/event requests must retain at least one bounded item when
    # the packet contains one. Trim lower-priority scalar detail first; only
    # then remove the oldest/least-verified event evidence.
    events = list(compacted.get("events") or [])
    minimum_events = 1 if events and requested_scopes.intersection(
        {"events", "current_news", "geopolitics"}
    ) else 0
    minimum_by_context: dict[str, int] = {}
    if "current_news" in requested_scopes and any(
        str(item.get("event_context") or "") == "news_radar_context"
        for item in events
    ):
        minimum_by_context["news_radar_context"] = 1
    if "geopolitics" in requested_scopes and any(
        str(item.get("event_context") or "") == "external_event_context"
        for item in events
    ):
        minimum_by_context["external_event_context"] = 1
    event_priority = {
        "unverified": 0,
        "discovered": 1,
        "secondary_corroborated": 2,
        "primary_verified": 3,
    }
    events.sort(
        key=lambda item: (
            event_priority.get(str(item.get("verification_state") or ""), 0),
            str(item.get("publisher_published_at") or item.get("index_seen_at") or ""),
            str(item.get("event_id") or ""),
        )
    )
    event_context_counts: dict[str, int] = {}
    for event in events:
        context = str(event.get("event_context") or "")
        event_context_counts[context] = event_context_counts.get(context, 0) + 1
    while not current.ready and len(events) > minimum_events:
        removable_index = next(
            (
                index
                for index, item in enumerate(events)
                if event_context_counts.get(str(item.get("event_context") or ""), 0)
                > minimum_by_context.get(str(item.get("event_context") or ""), 0)
            ),
            None,
        )
        if removable_index is None:
            break
        removed = events.pop(removable_index)
        event_id = str(removed.get("event_id") or "")
        removed_context = str(removed.get("event_context") or "")
        event_context_counts[removed_context] = max(
            0,
            event_context_counts.get(removed_context, 0) - 1,
        )
        compacted["events"] = [
            item for item in compacted.get("events") or []
            if str(item.get("event_id") or "") != event_id
        ]
        removed_events.append(event_id)
        omitted.append("events")
        reasons["events"] = "token_budget"
        current = refresh()

    # A very small test/deployment profile may not fit even the requested-scope
    # minimums. Prefer a degraded but explicit one-fact-per-domain packet over
    # a context overflow; the coverage metadata records this second-stage trim.
    if not current.ready:
        remaining_facts = list(compacted.get("facts") or [])
        remaining_counts: dict[str, int] = {}
        for fact in remaining_facts:
            domain = str(fact.get("domain") or "")
            remaining_counts[domain] = remaining_counts.get(domain, 0) + 1
        for fact in sorted(
            remaining_facts,
            key=lambda item: (
                removal_value_priority(item),
                domain_priority.get(str(item.get("domain") or ""), 4),
                str(item.get("fact_id") or ""),
            ),
        ):
            if current.ready:
                break
            if protected_from_compaction(fact):
                continue
            domain = str(fact.get("domain") or "")
            if remaining_counts.get(domain, 0) <= 1:
                continue
            fact_id = str(fact.get("fact_id") or "")
            compacted["facts"] = [
                item
                for item in compacted.get("facts") or []
                if str(item.get("fact_id") or "") != fact_id
            ]
            remaining_counts[domain] -= 1
            removed_facts.append(fact_id)
            omitted.append(domain)
            reasons[domain] = "token_budget_emergency_minimum"
            current = refresh()

    included = set(str(item) for item in coverage.get("included_sections") or [])
    omitted_set = set(str(item) for item in omitted)
    omitted_set.update(
        str(item) for item in coverage.get("partially_omitted_sections") or []
    )
    coverage["partially_omitted_sections"] = sorted(included.intersection(omitted_set))
    coverage["omitted_sections"] = sorted(omitted_set.difference(included))
    coverage["omission_reasons"] = dict(sorted((str(k), str(v)) for k, v in reasons.items()))
    _refresh_render_contract(compacted)
    current = refresh()
    return PacketCompactionResult(
        packet=compacted,
        original_preflight=original,
        compacted_preflight=current,
        removed_fact_ids=tuple(removed_facts),
        removed_event_ids=tuple(removed_events),
    )


def preflight_prompt_tokens(
    estimated_prompt_tokens: int,
    *,
    profile: ContextProfile,
    actual_context: int,
    reserved_output_tokens: int = DEFAULT_RESERVED_OUTPUT_TOKENS,
) -> TokenPreflight:
    context = max(1, int(actual_context))
    reserved = max(1, int(reserved_output_tokens))
    safety_margin = max(2_048, math.ceil(context * 0.10))
    context_capacity = max(0, context - reserved - safety_margin)
    effective_budget = min(profile.prompt_token_cap, context_capacity)
    ready = estimated_prompt_tokens <= effective_budget and effective_budget > 0
    if effective_budget <= 0:
        reason = "context_has_no_prompt_capacity"
    elif estimated_prompt_tokens > effective_budget:
        reason = "estimated_prompt_exceeds_effective_budget"
    else:
        reason = "within_effective_budget"
    return TokenPreflight(
        estimator=TOKEN_ESTIMATOR_VERSION,
        estimated_prompt_tokens=max(0, int(estimated_prompt_tokens)),
        actual_context=context,
        reserved_output_tokens=reserved,
        safety_margin=safety_margin,
        context_prompt_capacity=context_capacity,
        effective_prompt_budget=effective_budget,
        ready=ready,
        reason=reason,
    )
