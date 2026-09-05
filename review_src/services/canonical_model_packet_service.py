from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from core.line_model_contract import (
    ContextProfile,
    MODEL_FACT_PACKET_VERSION,
    build_model_fact_packet_v2,
    fact_supports_requested_scope,
    finalize_model_fact_packet_v2,
)
from core.public_url import normalize_public_https_url


CANONICAL_MODEL_PACKET_PROJECTION_VERSION = "CanonicalModelFactPacketV2ProjectionV2"
MAX_PACKET_FACTS = 96
MAX_PACKET_EVENTS = 8
_CANONICAL_SECTION_PROFILE = ContextProfile(
    name="canonical-section-projection-v1",
    prompt_token_cap=1_000_000,
    facts_total=80,
    facts_per_scope=24,
    events_total=0,
    events_per_scope=0,
    minimum_context=1,
    release_state="audit_only",
)
_CORPORATE_ACTION_DIRECT_FIELDS = {
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
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _event_rights(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip().startswith("{"):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _projected_fact_priority(source: Mapping[str, Any]) -> tuple[int, int, str]:
    domain = str(source.get("domain") or "")
    field = str(source.get("field") or "").lower()
    value = source.get("value")
    domain_priority = 5
    if domain == "institutional_context":
        domain_priority = 0 if field.startswith("flow.") else 1 if "estimated_cost" in field else 3
    elif domain == "technical":
        domain_priority = 0 if field.startswith("rsi.") else 1 if field.startswith("macd.") else 2
    elif domain == "valuation":
        domain_priority = 0
    scalar_priority = 0 if _finite(value) is not None else 1 if isinstance(value, str) else 2
    return domain_priority, scalar_priority, field


def _fact(
    facts: list[dict[str, Any]],
    *,
    entity: str,
    domain: str,
    field: str,
    value: Any,
    unit: str | None,
    period: str,
    trade_date: str,
    as_of: str,
    quality: str,
    availability_reason: str | None,
    source_fields: list[str],
    source_evidence_ids: list[str] | None = None,
    use_scope: list[str] | None = None,
) -> None:
    if len(facts) >= MAX_PACKET_FACTS:
        return
    fact_id = f"F{len(facts) + 1:03d}"
    scopes = list(use_scope or ["explanation"])
    if use_scope is None and quality in {"ok", "estimated"}:
        if _finite(value) is not None:
            scopes.append("numeric_claim")
        if isinstance(value, str) and value[:10] == trade_date and len(value) >= 10:
            scopes.append("date_claim")
    if quality not in {"ok", "estimated"}:
        scopes = ["limitation"]
    facts.append(
        {
            "fact_id": fact_id,
            "entity": entity,
            "domain": domain,
            "field": field,
            "value": value,
            "unit": unit,
            "currency": "TWD" if unit == "TWD" else None,
            "period": period,
            "trade_date": trade_date,
            "as_of": as_of,
            "authority_tier": "canonical_db",
            "quality": quality,
            "availability_reason": availability_reason,
            "use_scope": list(dict.fromkeys(scopes)),
            "source_fields": source_fields,
            "source_evidence_ids": list(source_evidence_ids or []),
        }
    )


def _entity_facts(
    facts: list[dict[str, Any]],
    entity_analysis: Mapping[str, Any],
    *,
    analysis_cutoff: str,
    fallback_evidence_ids: list[str],
) -> None:
    entity = entity_analysis.get("entity") if isinstance(entity_analysis.get("entity"), Mapping) else {}
    code = str(entity.get("code") or "")
    trade_date = str(entity_analysis.get("trade_date") or "")
    ohlcv = entity_analysis.get("ohlcv") if isinstance(entity_analysis.get("ohlcv"), Mapping) else {}
    for field, unit in (
        ("open", "TWD"),
        ("high", "TWD"),
        ("low", "TWD"),
        ("close", "TWD"),
        ("volume", "shares"),
    ):
        value = ohlcv.get(field)
        if _finite(value) is None:
            continue
        _fact(
            facts,
            entity=code,
            domain="official_ohlcv",
            field=field,
            value=value,
            unit=unit,
            period="daily",
            trade_date=trade_date,
            as_of=trade_date,
            quality="ok",
            availability_reason=None,
            source_fields=[f"entity_analyses[{code}].ohlcv.{field}"],
            source_evidence_ids=fallback_evidence_ids if field == "close" else [],
        )
    technical = (
        entity_analysis.get("technical_ensemble")
        if isinstance(entity_analysis.get("technical_ensemble"), Mapping)
        else {}
    )
    technical_quality = "ok" if technical.get("status") == "ok" else "estimated" if technical.get("status") == "partial" else "unavailable"
    if _finite(technical.get("overall_score")) is not None:
        _fact(
            facts,
            entity=code,
            domain="technical",
            field="technical_ensemble.overall_score",
            value=technical.get("overall_score"),
            unit="score",
            period="daily",
            trade_date=trade_date,
            as_of=trade_date,
            quality=technical_quality,
            availability_reason=technical.get("availability_reason"),
            source_fields=[f"technical_ensembles.{code}.overall_score"],
        )
    family_scores = technical.get("family_scores") if isinstance(technical.get("family_scores"), Mapping) else {}
    for family, value in sorted(family_scores.items()):
        if _finite(value) is None:
            continue
        _fact(
            facts,
            entity=code,
            domain="technical",
            field=f"technical_ensemble.{family}",
            value=value,
            unit="score",
            period="daily",
            trade_date=trade_date,
            as_of=trade_date,
            quality=technical_quality,
            availability_reason=technical.get("availability_reason"),
            source_fields=[f"technical_ensembles.{code}.family_scores.{family}"],
        )
    if technical_quality == "unavailable":
        _fact(
            facts,
            entity=code,
            domain="technical",
            field="availability",
            value=None,
            unit=None,
            period="daily",
            trade_date=trade_date,
            as_of=trade_date,
            quality="unavailable",
            availability_reason=str(technical.get("availability_reason") or "technical_ensemble_unavailable"),
            source_fields=[f"technical_ensembles.{code}.status"],
        )


def _corporate_action_facts(
    facts: list[dict[str, Any]],
    entity_analysis: Mapping[str, Any],
    *,
    analysis_cutoff: str,
) -> None:
    """Reserve bounded official price-basis facts before generic risk trimming."""

    sections = (
        entity_analysis.get("canonical_sections")
        if isinstance(entity_analysis.get("canonical_sections"), Mapping)
        else {}
    )
    safety = (
        sections.get("recommendation_safety")
        if isinstance(sections.get("recommendation_safety"), Mapping)
        else {}
    )
    action = (
        safety.get("corporate_action")
        if isinstance(safety.get("corporate_action"), Mapping)
        else {}
    )
    if (
        action.get("status") != "active_window"
        or action.get("confirmed") is not True
        or action.get("verification_status") != "official_verified"
    ):
        return
    entity = (
        entity_analysis.get("entity")
        if isinstance(entity_analysis.get("entity"), Mapping)
        else {}
    )
    code = str(entity.get("code") or "")
    trade_date = str(entity_analysis.get("trade_date") or "")
    field_contract = (
        ("action_date", None, ["explanation", "date_claim"]),
        ("action_type", None, ["explanation"]),
        ("adjustment_method", None, ["explanation"]),
        ("stock_distribution_ratio", "ratio", ["explanation", "numeric_claim"]),
        ("ratio_unit", None, ["explanation"]),
        ("cash_dividend_per_share", "TWD", ["explanation", "numeric_claim"]),
        ("share_count_factor", "ratio", ["explanation", "numeric_claim"]),
        ("pre_event_price_multiplier", "ratio", ["explanation", "numeric_claim"]),
        ("verification_status", None, ["explanation"]),
        ("source_id", None, ["explanation"]),
    )
    for field, unit, use_scope in field_contract:
        value = action.get(field)
        if value is None:
            continue
        _fact(
            facts,
            entity=code,
            domain="recommendation_safety",
            field=f"corporate_action.{field}",
            value=value,
            unit=unit,
            period="corporate_action",
            trade_date=trade_date,
            as_of=analysis_cutoff,
            quality="ok",
            availability_reason=None,
            source_fields=[
                f"entity_analyses[{code}].canonical_sections."
                f"recommendation_safety.corporate_action.{field}"
            ],
            use_scope=use_scope,
        )


def _canonical_section_facts(
    facts: list[dict[str, Any]],
    entity_analysis: Mapping[str, Any],
    *,
    requested_scopes: list[str],
    analysis_cutoff: str,
) -> None:
    sections = (
        entity_analysis.get("canonical_sections")
        if isinstance(entity_analysis.get("canonical_sections"), Mapping)
        else {}
    )
    if not sections:
        return
    entity = entity_analysis.get("entity") if isinstance(entity_analysis.get("entity"), Mapping) else {}
    code = str(entity.get("code") or "")
    trade_date = str(entity_analysis.get("trade_date") or "")
    projected = build_model_fact_packet_v2(
        {"trade_date": trade_date, **dict(sections)},
        focus="overview",
        depth="comprehensive",
        profile=_CANONICAL_SECTION_PROFILE,
        requested_scopes=requested_scopes,
    )
    for source in sorted(projected.packet.get("facts") or [], key=_projected_fact_priority):
        source_field = str(source.get("field") or "")
        if (
            len(facts) >= MAX_PACKET_FACTS
            or source_field == "availability"
            or source.get("value") is None
            or (
                str(source.get("domain") or "") == "recommendation_safety"
                and source_field
                in {
                    f"corporate_action.{field}"
                    for field in _CORPORATE_ACTION_DIRECT_FIELDS
                }
            )
        ):
            continue
        fact = dict(source)
        fact["fact_id"] = f"F{len(facts) + 1:03d}"
        fact["entity"] = code
        fact["trade_date"] = trade_date
        # Preserve source-period semantics (notably cost sample facts require
        # as_of == trade_date). The exact artifact cutoff remains in request.
        fact["as_of"] = str(source.get("as_of") or trade_date)
        fact["source_fields"] = [
            f"entity_analyses[{code}].canonical_sections."
            f"{source.get('domain')}.{source.get('field')}"
        ]
        fact["source_evidence_ids"] = []
        facts.append(fact)


def build_canonical_model_fact_packet_v2(
    artifact_response: Mapping[str, Any],
    *,
    requested_scopes: Iterable[str] = (),
    image_typed_observations: Mapping[str, Any] | None = None,
    event_records: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Project one sealed Web/LINE artifact into the single candidate model packet."""

    analysis = artifact_response.get("analysis") if isinstance(artifact_response.get("analysis"), Mapping) else {}
    analysis_id = str(artifact_response.get("analysis_id") or analysis.get("analysis_id") or "")
    cutoff = str(artifact_response.get("analysis_cutoff") or analysis.get("analysis_cutoff") or "")
    if not analysis_id or not cutoff:
        raise ValueError("sealed artifact identity and analysis_cutoff are required")
    profile = str(analysis.get("profile") or "focused")
    depth = "comprehensive" if profile == "comprehensive" else "focused"
    scopes = list(dict.fromkeys(str(item) for item in requested_scopes if str(item)))
    if not scopes:
        scopes = ["price", "technical", "events", "risk"]
    entities = [dict(item) for item in list(analysis.get("target_entities") or []) if isinstance(item, Mapping)]
    if not entities and isinstance(analysis.get("stock"), Mapping):
        entities = [dict(analysis.get("stock") or {})]
    trade_date = str(analysis.get("trade_date") or "")
    evidence_ids = [str(item) for item in list(artifact_response.get("evidence_ids") or analysis.get("evidence_ids") or [])]
    facts: list[dict[str, Any]] = []
    _fact(
        facts,
        entity=str((entities[0] if entities else {}).get("code") or "market"),
        domain="analysis_identity",
        field="analysis_cutoff",
        value=cutoff,
        unit=None,
        period="point_in_time",
        trade_date=trade_date,
        as_of=cutoff,
        quality="ok",
        availability_reason=None,
        source_fields=["analysis_cutoff"],
        use_scope=["explanation", "date_claim"],
    )
    entity_analyses = [
        item for item in list(analysis.get("entity_analyses") or []) if isinstance(item, Mapping)
    ]
    if not entity_analyses and analysis:
        entity_analyses = [
            {
                "entity": entities[0] if entities else analysis.get("stock") or {},
                "trade_date": trade_date,
                "ohlcv": analysis.get("ohlcv") or {},
                "technical_ensemble": analysis.get("technical_ensemble") or {},
            }
        ]
    for entity_analysis in entity_analyses:
        _entity_facts(
            facts,
            entity_analysis,
            analysis_cutoff=cutoff,
            fallback_evidence_ids=evidence_ids,
        )
        _corporate_action_facts(
            facts,
            entity_analysis,
            analysis_cutoff=cutoff,
        )
        _canonical_section_facts(
            facts,
            entity_analysis,
            requested_scopes=scopes,
            analysis_cutoff=cutoff,
        )

    image = dict(image_typed_observations or {})
    if image:
        image["estimated"] = True
        image["can_enter_referee"] = False
    if image:
        for collection_name in ("indicators", "price_values"):
            for index, item in enumerate(list(image.get(collection_name) or [])[:12]):
                if not isinstance(item, Mapping) or _finite(item.get("value")) is None:
                    continue
                _fact(
                    facts,
                    entity=str((entities[0] if entities else {}).get("code") or "unknown"),
                    domain="image_observation",
                    field=f"{collection_name}.{index}.{item.get('name') or item.get('label') or 'value'}",
                    value=item.get("value"),
                    unit="estimated",
                    period=str(image.get("timeframe") or "image_visible"),
                    trade_date=trade_date,
                    as_of=cutoff,
                    quality="estimated",
                    availability_reason="model_read_image_observation",
                    source_fields=[f"image_typed_observations.{collection_name}[{index}]"],
                )

    events: list[dict[str, Any]] = []
    allowed_source_event_ids = {
        str(item) for item in list(artifact_response.get("event_ids") or analysis.get("event_ids") or [])
    }
    for source in event_records:
        source_event_id = str(source.get("event_id") or "")
        if source_event_id not in allowed_source_event_ids or len(events) >= MAX_PACKET_EVENTS:
            continue
        rights = _event_rights(source.get("rights"))
        source_class = str(source.get("source_class") or "")
        canonical_official = source_class in {
            "canonical_official",
            "canonical_normalized_supplemental",
        } and str(source.get("rights") or "").startswith("official")
        source_url = normalize_public_https_url(source.get("source_url"))
        if rights:
            if rights.get("allow_model") is not True:
                continue
            allow_display = bool(source_url and rights.get("allow_display") is True)
            citation_required = bool(rights.get("attribution_required"))
            if citation_required and not allow_display:
                continue
        elif canonical_official:
            allow_display = False
            citation_required = False
        else:
            # Unknown or metadata-only rights never become model-usable event text.
            continue
        events.append(
            {
                "event_id": f"E{len(events) + 1:03d}",
                "source_event_id": source_event_id,
                "event_context": "canonical_event_evidence",
                "title": str(source.get("untrusted_text") or source.get("title") or "")[:160],
                "publisher": str(source.get("publisher") or ""),
                "publisher_published_at": source.get("publisher_published_at"),
                "index_seen_at": source.get("index_seen_at"),
                "retrieved_at": source.get("retrieved_at"),
                "available_at": source.get("available_at"),
                "verification_state": str(source.get("verification_state") or "unverified"),
                "source_class": source_class,
                "untrusted_text": True,
                "source_url": source_url or "",
                "allow_display": allow_display,
                "citation_required": citation_required,
                "attribution_required": bool(rights.get("attribution_required")),
                "source_policy_version": str(
                    rights.get("policy_version")
                    or analysis.get("event_scan", {}).get("source_policy_version")
                    or ""
                ),
                "dedup_cluster": str(source.get("dedup_cluster") or ""),
            }
        )

    omissions = [
        dict(item)
        for item in list(artifact_response.get("omissions") or analysis.get("omissions") or [])
        if isinstance(item, Mapping)
    ]
    conflicts = [
        dict(item)
        for item in list(artifact_response.get("conflicts") or analysis.get("conflicts") or [])
        if isinstance(item, Mapping)
    ]
    omission_reason_by_scope = {
        str(item.get("scope") or ""): str(item.get("reason") or "")
        for item in omissions
        if str(item.get("scope") or "")
    }
    event_scopes = {"events", "current_news", "geopolitics"}
    for scope in scopes:
        has_fact = any(fact_supports_requested_scope(fact, scope) for fact in facts)
        has_event = scope in event_scopes and bool(events)
        if has_fact or has_event:
            continue
        _fact(
            facts,
            entity=str((entities[0] if entities else {}).get("code") or "market"),
            domain=scope,
            field="availability",
            value=None,
            unit=None,
            period="current",
            trade_date=trade_date,
            as_of=cutoff,
            quality="unavailable",
            availability_reason=(
                omission_reason_by_scope.get(scope) or "no_eligible_canonical_facts"
            ),
            source_fields=[f"omissions.{scope}"],
            use_scope=["limitation"],
        )

    referee = analysis.get("referee") if isinstance(analysis.get("referee"), Mapping) else {}
    event_scan = analysis.get("event_scan") if isinstance(analysis.get("event_scan"), Mapping) else {}
    packet = {
        "contract_version": MODEL_FACT_PACKET_VERSION,
        "projection_version": CANONICAL_MODEL_PACKET_PROJECTION_VERSION,
        "artifact_identity": {
            "analysis_id": analysis_id,
            "snapshot_id": str(artifact_response.get("snapshot_id") or analysis.get("snapshot_id") or ""),
            "canonical_answer_text_hash": str(
                artifact_response.get("base_canonical_answer_text_hash")
                or artifact_response.get("canonical_answer_text_hash")
                or ""
            ),
        },
        "request": {
            "depth": depth,
            "scopes": scopes,
            "analysis_cutoff": cutoff,
            "trade_date": trade_date,
            "locale": "zh-TW",
        },
        "target_entities": entities,
        "comparison_set": [dict(item) for item in list(analysis.get("comparison_set") or []) if isinstance(item, Mapping)],
        "referee": {
            "immutable": True,
            "decision_ready": bool(referee.get("decision_ready")),
            "main_status": referee.get("main_status"),
            "can_be_overridden_by_model": False,
        },
        "facts": facts,
        "events": events,
        "factor_coverage": dict(analysis.get("factor_scores") or {}),
        "conversation_projection": dict(analysis.get("conversation_projection") or {}),
        "image_typed_observations": image,
        "omissions": omissions,
        "conflicts": conflicts,
        "coverage": {
            **dict(event_scan.get("coverage") or {}),
            "artifact_validity": str(artifact_response.get("validity") or ""),
            "fact_count": len(facts),
            "event_count": len(events),
        },
    }
    finalized = finalize_model_fact_packet_v2(packet)
    finalized["packet_digest"] = hashlib.sha256(_canonical_json(finalized).encode("utf-8")).hexdigest()
    return finalized
