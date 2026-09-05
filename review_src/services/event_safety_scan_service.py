from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from analysis.materiality_classification_v1 import classify_content_materiality
from core.market_timing import available_at_or_before_cutoff, parse_market_timestamp
from core.single_track_v3_event_source_plan import REQUIRED_EVENT_SCAN_SCOPES
from repository.single_track_v3_repository import (
    record_event_scan,
    supersede_artifact_with_events,
    upsert_event_evidence,
)


TPE = ZoneInfo("Asia/Taipei")
EVENT_SCAN_VERSION = "EventSafetyScanV1"
SOURCE_POLICY_VERSION = "SourceAuthorityPolicyV1"
REQUIRED_SCAN_SCOPES = REQUIRED_EVENT_SCAN_SCOPES
_AUTHORITY_RANK = {
    "canonical_official": 5,
    "canonical_normalized_supplemental": 4,
    "licensed_secondary": 3,
    "news_radar": 2,
    "model_inference": 1,
}
_INCOMPLETE_STATUSES = {
    "timeout",
    "rate_limited",
    "offline",
    "unavailable",
    "failed",
    "error",
    "incomplete",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()}"


def _bounded_text(value: Any, maximum: int = 2000) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _source_class(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("source_class") or "").strip()
    if explicit in _AUTHORITY_RANK:
        return explicit
    quality = str(row.get("source_quality") or "").strip().lower()
    verification = str(
        row.get("verification_state") or row.get("verification_status") or ""
    ).strip().lower()
    source_id = str(row.get("source_id") or row.get("source") or "").upper()
    if quality == "official" and any(
        marker in source_id for marker in ("MOPS", "TWSE", "TPEX", "GOV", "FED")
    ):
        return "canonical_official"
    if quality == "official":
        return "canonical_normalized_supplemental"
    if quality == "licensed":
        return "licensed_secondary"
    if verification == "unverified" or quality == "supplemental":
        return "news_radar"
    return "model_inference"


def _verification_state(row: Mapping[str, Any], source_class: str) -> str:
    explicit = str(
        row.get("verification_state") or row.get("verification_status") or ""
    ).strip().lower()
    if explicit in {"rejected", "conflicted"}:
        return explicit
    if source_class in {"canonical_official", "canonical_normalized_supplemental"}:
        return "verified"
    if source_class == "licensed_secondary":
        reliability = float(row.get("reliability_score") or row.get("confidence") or 0)
        reference_value = float(row.get("reference_value_score") or 0)
        qualified = reliability >= 0.85 and reference_value >= 0.8
        return "verified" if explicit == "verified" or qualified else "unverified"
    return "unverified"


def _materiality(row: Mapping[str, Any]) -> str:
    explicit = str(row.get("materiality") or "").strip().lower()
    if explicit in {"material", "non_material", "unknown"}:
        return explicit
    attention = str(row.get("attention_level") or "").strip().lower()
    event_type = str(row.get("event_type") or row.get("subject") or "").casefold()
    material_markers = (
        "material",
        "重大",
        "convertible_bond",
        "可轉債",
        "dilution",
        "增資",
        "sanction",
        "出口管制",
        "earnings_surprise",
        "merger",
        "acquisition",
    )
    if attention == "attention" or any(marker in event_type for marker in material_markers):
        return "material"
    return "unknown"


def _confidence(row: Mapping[str, Any], source_class: str) -> float:
    raw = row.get("confidence")
    labels = {"high": 0.9, "medium": 0.7, "low": 0.4, "unavailable": 0.0}
    if isinstance(raw, str) and raw.strip().lower() in labels:
        value = labels[raw.strip().lower()]
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = {
                "canonical_official": 1.0,
                "canonical_normalized_supplemental": 0.9,
                "licensed_secondary": 0.8,
                "news_radar": 0.0,
                "model_inference": 0.0,
            }[source_class]
    return min(1.0, max(0.0, value))


def _normalize_event(
    row: Mapping[str, Any],
    entity_refs: list[str],
    *,
    source_coverage_complete: bool,
) -> dict[str, Any]:
    source_class = _source_class(row)
    title = _bounded_text(row.get("title") or row.get("subject") or row.get("explanation"))
    event_type = str(row.get("event_type") or row.get("article_code") or "unknown")
    source_id = str(row.get("source_id") or row.get("source") or "unknown")
    publisher = str(row.get("publisher") or row.get("company_name") or source_id)
    source_url = str(row.get("source_url") or row.get("url") or "")
    available_at = str(row.get("available_at") or row.get("retrieved_at") or "")
    published_at = row.get("publisher_published_at") or row.get("published_at")
    references = [
        str(item).zfill(4) if str(item).isdigit() else str(item)
        for item in (row.get("entity_refs") or ([row.get("code")] if row.get("code") else entity_refs))
        if str(item or "").strip()
    ]
    fingerprint = str(row.get("fingerprint") or row.get("content_fingerprint") or "")
    if not fingerprint:
        fingerprint = hashlib.sha256(
            _canonical_json(
                {
                    "entity_refs": sorted(references),
                    "event_type": event_type,
                    "publisher": publisher.casefold(),
                    "published_at": published_at,
                    "title": title.casefold(),
                }
            ).encode("utf-8")
        ).hexdigest()
    event_id = str(row.get("event_id") or row.get("event_key") or "")
    if not event_id:
        event_id = _stable_id("event", {"source_id": source_id, "fingerprint": fingerprint})
    rights = row.get("rights")
    if isinstance(rights, Mapping):
        rights_text = _canonical_json(dict(rights))
    else:
        rights_text = str(rights or row.get("license_class") or "metadata_only")
    direction = str(row.get("direction") or "unknown").strip().lower()
    if direction not in {"positive", "negative", "mixed", "neutral", "unknown"}:
        direction = "unknown"
    materiality = _materiality(row)
    explicit_relationship = str(
        row.get("target_relationship_type") or ""
    ).strip()
    official_direct = bool(
        references
        and set(references) <= set(entity_refs)
        and source_class == "canonical_official"
        and any(marker in source_id.upper() for marker in ("MOPS", "TWSE", "TPEX"))
    )
    relationship = explicit_relationship or (
        "direct_company" if official_direct else "unresolved"
    )
    entity_resolution_state = str(
        row.get("entity_resolution_state")
        or ("resolved" if references and set(references) <= set(entity_refs) else "unresolved")
    )
    materiality_candidate = classify_content_materiality(
        {
            "verification_state": _verification_state(row, source_class),
            "source_coverage_complete": source_coverage_complete,
            "entity_resolution_state": entity_resolution_state,
            "revision_complete": row.get("revision_complete") is True,
            "target_relationship_type": relationship,
            "event_category": row.get("event_category") or event_type,
            "fact_predicates": row.get("fact_predicates") or [],
            "target_relevance": row.get("relevance") or 0.0,
        }
    )
    confidence = _confidence(row, source_class)
    try:
        magnitude = float(row.get("magnitude"))
    except (TypeError, ValueError):
        magnitude = 1.0 if materiality == "material" else 0.0
    try:
        relevance = float(row.get("relevance"))
    except (TypeError, ValueError):
        relevance = 1.0 if references else 0.5
    try:
        directness = float(row.get("directness"))
    except (TypeError, ValueError):
        directness = 1.0 if row.get("code") or row.get("entity_refs") else 0.5
    return {
        "event_id": event_id,
        "entity_refs": sorted(set(references)),
        "event_type": event_type,
        "source_id": source_id,
        "source_class": source_class,
        "publisher": publisher,
        "source_url": source_url,
        "publisher_published_at": published_at,
        "index_seen_at": row.get("index_seen_at"),
        "retrieved_at": str(row.get("retrieved_at") or row.get("fetched_at") or available_at),
        "available_at": available_at,
        "effective_tw_trade_date": row.get("effective_tw_trade_date"),
        "verification_state": _verification_state(row, source_class),
        "rights": rights_text,
        "untrusted_text": title,
        "fingerprint": fingerprint,
        "dedup_cluster": str(row.get("dedup_cluster") or fingerprint),
        "relevance": min(1.0, max(0.0, relevance)),
        "directness": min(1.0, max(0.0, directness)),
        "materiality": materiality,
        "content_materiality": materiality_candidate["content_materiality"],
        "materiality_contract_version": materiality_candidate["contract_version"],
        "candidate_regime_hint": materiality_candidate["candidate_regime_hint"],
        "formal_direction_weight": 0.0,
        "eligible_for_weight": False,
        "magnitude": min(1.0, max(0.0, magnitude)),
        "surprise": row.get("surprise"),
        "direction": direction,
        "confidence": confidence,
        "horizon": str(row.get("horizon") or row.get("time_horizon") or "unknown"),
        "affected_claim_ids": list(row.get("affected_claim_ids") or []),
        "invalidation_scope": str(row.get("invalidation_scope") or "next_day_outlook"),
        "corroborating_event_ids": list(row.get("corroborating_event_ids") or []),
        "recorded_at": str(row.get("recorded_at") or row.get("retrieved_at") or available_at),
    }


def _deduplicate(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        clusters[str(event["dedup_cluster"])].append(event)
    selected: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for cluster_id, members in sorted(clusters.items()):
        ordered = sorted(
            members,
            key=lambda item: (
                _AUTHORITY_RANK.get(str(item["source_class"]), 0),
                str(item["verification_state"]) == "verified",
                float(item["confidence"]),
                str(item["available_at"]),
            ),
            reverse=True,
        )
        primary = dict(ordered[0])
        primary["corroborating_event_ids"] = sorted(
            set(primary.get("corroborating_event_ids") or [])
            | {str(item["event_id"]) for item in ordered[1:]}
        )
        verified_directions = {
            str(item["direction"])
            for item in ordered
            if item["verification_state"] == "verified"
            and item["direction"] in {"positive", "negative"}
        }
        if verified_directions == {"positive", "negative"}:
            primary["direction"] = "mixed"
            conflicts.append(
                {
                    "dedup_cluster": cluster_id,
                    "event_ids": sorted(str(item["event_id"]) for item in ordered),
                    "reason": "verified_direction_conflict",
                }
            )
        selected.append(primary)
    selected.sort(key=lambda item: (str(item["available_at"]), str(item["event_id"])))
    return selected, conflicts


def build_event_safety_scan(
    *,
    entity_refs: Iterable[str],
    analysis_cutoff: str,
    events: Iterable[Mapping[str, Any]],
    source_results: Mapping[str, str],
    previous_artifact_cutoff: str | None = None,
    request_received_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Normalize, PIT-filter, deduplicate, and classify one complete safety scan."""

    cutoff = parse_market_timestamp(analysis_cutoff)
    if cutoff is None:
        raise ValueError("analysis_cutoff must be an offset-aware ISO-8601 timestamp")
    entities = sorted({str(item) for item in entity_refs if str(item)})
    coverage = {
        scope: str(source_results.get(scope) or "missing")
        for scope in REQUIRED_SCAN_SCOPES
    }
    incomplete_scopes = [
        scope
        for scope, status in coverage.items()
        if status.strip().lower() in _INCOMPLETE_STATUSES or status == "missing"
    ]
    normalized: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    for source in events:
        event = _normalize_event(
            source,
            entities,
            source_coverage_complete=not incomplete_scopes,
        )
        if not available_at_or_before_cutoff(event.get("available_at"), analysis_cutoff):
            omissions.append(
                {"event_id": event["event_id"], "reason": "fact_after_cutoff"}
            )
            continue
        normalized.append(event)
    deduplicated, conflicts = _deduplicate(normalized)

    verified_material = [
        event
        for event in deduplicated
        if event["verification_state"] == "verified"
        and event["materiality"] == "material"
    ]
    if incomplete_scopes:
        scan_state = "scan_incomplete"
    elif conflicts:
        scan_state = "pending_reconciliation"
    elif verified_material:
        scan_state = "verified_material"
    else:
        scan_state = "verified_none"

    direction_scores = []
    for event in verified_material:
        sign = 1.0 if event["direction"] == "positive" else -1.0 if event["direction"] == "negative" else 0.0
        direction_scores.append(sign * float(event["magnitude"] or 0) * float(event["confidence"] or 0))
    event_direction_score = (
        sum(direction_scores) / len(direction_scores) if direction_scores else 0.0
    )

    superseding_ids: list[str] = []
    previous_cutoff = parse_market_timestamp(previous_artifact_cutoff)
    if previous_cutoff is not None:
        superseding_ids = sorted(
            str(event["event_id"])
            for event in verified_material
            if parse_market_timestamp(event["available_at"]) is not None
            and parse_market_timestamp(event["available_at"]) > previous_cutoff
        )
    watermark = max(
        (str(event["available_at"]) for event in deduplicated),
        default=None,
    )
    finished = completed_at or datetime.now(TPE).isoformat(timespec="seconds")
    received = request_received_at or analysis_cutoff
    scan_identity = {
        "entity_refs": entities,
        "analysis_cutoff": analysis_cutoff,
        "event_watermark": watermark,
        "source_policy_version": SOURCE_POLICY_VERSION,
        "event_ids": [event["event_id"] for event in deduplicated],
    }
    return {
        "scan_id": _stable_id("event-scan", scan_identity),
        "scan_version": EVENT_SCAN_VERSION,
        "entity_refs": entities,
        "request_received_at": received,
        "analysis_cutoff": analysis_cutoff,
        "event_watermark": watermark,
        "scan_state": scan_state,
        "source_policy_version": SOURCE_POLICY_VERSION,
        "coverage": coverage,
        "incomplete_scopes": incomplete_scopes,
        "omissions": omissions,
        "conflicts": conflicts,
        "events": deduplicated,
        "verified_material_event_ids": [event["event_id"] for event in verified_material],
        "superseded_by_event_ids": superseding_ids,
        "event_direction_score": event_direction_score,
        # V11 Stage 0 approval authorizes the candidate contract, not a
        # non-zero release. Keep every news direction contribution at zero
        # until a separately reviewed predictive release gate succeeds.
        "event_direction_weight": 0.0,
        "event_direction_weight_state": "shadow_zero_weight",
        "unreleased_material_event_family_weight": (
            0.35 if verified_material else 0.0
        ),
        "invalidation_independent_of_direction": True,
        "high_confidence_allowed": scan_state not in {"scan_incomplete", "pending_reconciliation"},
        "completed_at": finished,
    }


def persist_event_safety_scan(
    conn,
    scan: Mapping[str, Any],
    *,
    previous_analysis_id: str | None = None,
) -> dict[str, Any]:
    """Persist normalized metadata and apply an independently validated supersession."""

    events = [dict(item) for item in scan.get("events") or []]
    canonical_events = [
        event
        for event in events
        if event.get("source_class")
        in {
            "canonical_official",
            "canonical_normalized_supplemental",
            "licensed_secondary",
        }
        and event.get("verification_state") != "unverified"
    ]
    written = upsert_event_evidence(conn, canonical_events)
    record_event_scan(conn, scan)
    superseded_ids = list(scan.get("superseded_by_event_ids") or [])
    if previous_analysis_id and superseded_ids:
        supersede_artifact_with_events(
            conn,
            previous_analysis_id,
            superseded_ids,
            reason="verified material event became available after the artifact cutoff",
        )
    return {
        "event_rows_written": written,
        "noncanonical_event_rows_skipped": len(events) - len(canonical_events),
        "scan_rows_written": 1,
        "artifact_superseded": bool(previous_analysis_id and superseded_ids),
        "superseded_by_event_ids": superseded_ids,
    }
