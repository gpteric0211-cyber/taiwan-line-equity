from __future__ import annotations

"""Deterministic research-metadata reconciliation into sealed event revisions.

Retrieval never promotes on its own.  This task requires a qualifying primary
or licensed source, keeps Radar directionless, stores no raw body, and still
does not write canonical_event_evidence.  Content/materiality assessment is a
separate downstream stage.
"""

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from analysis.event_revision_classification_v1 import classify_event_revision
from analysis.materiality_classification_v1 import classify_content_materiality
from repository.single_track_v3_assessment_repository import (
    create_event_cluster,
    create_event_revision,
)
from repository.single_track_v3_reconciliation_repository import (
    event_cluster_by_dedup_key,
    extend_event_cluster_observation,
    latest_event_revision_for_cluster,
    link_research_items_to_event_revision,
)
from repository.single_track_v3_research_repository import (
    research_news_items_available_at,
)


EVENT_RECONCILIATION_CONTRACT_VERSION = "SingleTrackV3EventReconciliationV1"
_REQUIRED_TABLES = {"research_news_item", "event_cluster", "event_revision"}
_PRIMARY_CLASSES = {"canonical_official", "canonical_normalized_supplemental"}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
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
    return parsed


def event_reconciler_schema_ready(conn: sqlite3.Connection) -> bool:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    return _REQUIRED_TABLES <= tables


def _eligible_primary(item: Mapping[str, Any]) -> bool:
    source_class = str(item.get("source_class") or "")
    verification = str(item.get("verification_state") or "")
    return (
        source_class in _PRIMARY_CLASSES and verification == "primary_verified"
    ) or (
        source_class == "licensed_secondary"
        and verification == "secondary_corroborated"
    )


def _event_type(items: list[Mapping[str, Any]]) -> str:
    source_ids = {str(item.get("source_id") or "").upper() for item in items}
    if any("MOPS" in source_id for source_id in source_ids):
        return "company_disclosure"
    if source_ids & {
        "EXECUTIVE_YUAN_NEWS",
        "EXECUTIVE_YUAN_MINISTRY_NEWS",
        "MOEA_NEWS",
        "FEDERAL_RESERVE_MONETARY_POLICY_RSS",
        "US_TREASURY_PRESS_RELEASE_INDEX",
        "OFAC_RECENT_ACTIONS_INDEX",
        "BIS_PRESS_RELEASE_INDEX",
    }:
        return "government_policy"
    return "research_event"


def _source_ref(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "available_at": item.get("available_at"),
        "content_hash": item.get("content_hash"),
        "event_fingerprint": item.get("event_fingerprint"),
        "news_item_id": item.get("news_item_id"),
        "publisher": item.get("publisher"),
        "publisher_published_at": item.get("publisher_published_at"),
        "publisher_published_date": item.get("publisher_published_date"),
        "source_class": item.get("source_class"),
        "source_id": item.get("source_id"),
        "source_policy_version": item.get("source_policy_version"),
        "source_url": item.get("source_url"),
        "title": str(item.get("title") or "")[:300],
        "verification_state": item.get("verification_state"),
    }


def reconcile_research_events(
    conn: sqlite3.Connection,
    *,
    analysis_cutoff: str,
    sealed_at: str,
    limit: int = 256,
    source_coverage_complete: bool = False,
    entity_resolution_verified: bool = False,
) -> dict[str, Any]:
    """Reconcile a bounded PIT-visible set in one caller-owned savepoint."""

    if not event_reconciler_schema_ready(conn):
        raise RuntimeError("Single-Track event reconciliation schema is not initialized")
    cutoff = _aware(analysis_cutoff, "analysis_cutoff")
    sealed = _aware(sealed_at, "sealed_at")
    if sealed < cutoff:
        raise ValueError("sealed_at cannot precede the reconciliation cutoff")
    bounded_limit = int(limit)
    if bounded_limit < 1 or bounded_limit > 512:
        raise ValueError("limit must be between 1 and 512")
    visible = research_news_items_available_at(conn, analysis_cutoff)
    pending = [item for item in visible if item.get("event_revision_id") is None]
    if len(pending) > bounded_limit:
        raise ValueError("pending reconciliation set exceeds the bounded limit")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in pending:
        groups[str(item["dedup_cluster"])].append(item)

    promoted_revision_ids: list[str] = []
    linked_item_ids: list[str] = []
    unresolved: list[dict[str, Any]] = []
    revision_classifications: list[dict[str, Any]] = []
    savepoint = "single_track_event_reconciliation"
    conn.execute(f'SAVEPOINT "{savepoint}"')
    try:
        for dedup_key, members in sorted(groups.items()):
            entity_sets = {
                tuple(sorted(str(value) for value in member.get("entity_refs") or []))
                for member in members
            }
            if len(entity_sets) != 1 or not next(iter(entity_sets), ()):
                unresolved.append(
                    {
                        "dedup_cluster": dedup_key,
                        "reason_code": "event_entity_conflict",
                        "news_item_ids": sorted(str(item["news_item_id"]) for item in members),
                    }
                )
                continue
            entity_refs = list(next(iter(entity_sets)))
            qualifying = [item for item in members if _eligible_primary(item)]
            existing_cluster = event_cluster_by_dedup_key(conn, dedup_key)
            if not qualifying and existing_cluster is None:
                unresolved.append(
                    {
                        "dedup_cluster": dedup_key,
                        "reason_code": "unverified_radar",
                        "news_item_ids": sorted(str(item["news_item_id"]) for item in members),
                    }
                )
                continue
            event_type = (
                str(existing_cluster["event_type"])
                if existing_cluster is not None
                else _event_type(members)
            )
            first_available = min(str(item["available_at"]) for item in members)
            last_seen = max(str(item["last_retrieved_at"]) for item in members)
            if existing_cluster is None:
                cluster_id = f"event-cluster:{_digest({'dedup_key': dedup_key})}"
                cluster = create_event_cluster(
                    conn,
                    {
                        "event_cluster_id": cluster_id,
                        "dedup_key": dedup_key,
                        "event_type": event_type,
                        "entity_refs": entity_refs,
                        "cluster_state": "active",
                        "first_available_at": first_available,
                        "last_seen_at": last_seen,
                        "created_at": sealed_at,
                        "updated_at": sealed_at,
                    },
                    ensure_schema=False,
                )
                latest = None
            else:
                cluster = existing_cluster
                if cluster["event_type"] != event_type:
                    unresolved.append(
                        {
                            "dedup_cluster": dedup_key,
                            "reason_code": "event_type_conflict",
                            "news_item_ids": sorted(str(item["news_item_id"]) for item in members),
                        }
                    )
                    continue
                if set(str(value) for value in cluster.get("entity_refs") or []) != set(entity_refs):
                    unresolved.append(
                        {
                            "dedup_cluster": dedup_key,
                            "reason_code": "event_entity_conflict",
                            "news_item_ids": sorted(str(item["news_item_id"]) for item in members),
                        }
                    )
                    continue
                extend_event_cluster_observation(
                    conn,
                    event_cluster_id=str(cluster["event_cluster_id"]),
                    entity_refs=entity_refs,
                    last_seen_at=max(last_seen, str(cluster["last_seen_at"])),
                    updated_at=sealed_at,
                )
                latest = latest_event_revision_for_cluster(
                    conn, str(cluster["event_cluster_id"])
                )
            if not qualifying and latest is None:
                unresolved.append(
                    {
                        "dedup_cluster": dedup_key,
                        "reason_code": "unverified_radar",
                        "news_item_ids": sorted(str(item["news_item_id"]) for item in members),
                    }
                )
                continue
            if not qualifying and latest is not None:
                revision = latest
            else:
                previous_refs = list(latest.get("source_refs") or []) if latest else []
                refs_by_item = {
                    str(ref.get("news_item_id")): dict(ref)
                    for ref in previous_refs
                    if isinstance(ref, Mapping) and ref.get("news_item_id")
                }
                for member in members:
                    refs_by_item[str(member["news_item_id"])] = _source_ref(member)
                source_refs = [refs_by_item[key] for key in sorted(refs_by_item)]
                key_points = sorted(
                    {
                        str(member.get("title") or "").strip()[:300]
                        for member in members
                        if str(member.get("title") or "").strip()
                    }
                )[:12]
                relationship = (
                    "direct_company"
                    if event_type == "company_disclosure" and entity_resolution_verified
                    else "unresolved"
                )
                materiality_candidate = classify_content_materiality(
                    {
                        "verification_state": "verified",
                        "source_coverage_complete": source_coverage_complete,
                        "entity_resolution_state": (
                            "resolved" if entity_resolution_verified else "unresolved"
                        ),
                        "revision_complete": bool(source_refs and key_points),
                        "target_relationship_type": relationship,
                        "event_category": event_type,
                        "fact_predicates": [],
                        "target_relevance": 1.0 if relationship == "direct_company" else 0.0,
                    }
                )
                previous_relation_input = (
                    {
                        "key_points": list(latest.get("key_points") or []),
                        "source_refs": list(latest.get("source_refs") or []),
                        "core_fact_digest": _digest(
                            list(latest.get("key_points") or [])
                        ),
                        "headline": next(iter(latest.get("key_points") or []), None),
                        "price_reaction": dict(latest.get("price_reaction") or {}),
                        "target_direction": "unknown",
                        "verification_state": latest.get("verification_state"),
                    }
                    if latest
                    else None
                )
                current_relation_input = {
                    "key_points": key_points,
                    "source_refs": source_refs,
                    "core_fact_digest": _digest(key_points),
                    "headline": next(iter(key_points), None),
                    "price_reaction": {},
                    "target_direction": "unknown",
                    "verification_state": "verified",
                }
                revision_relation = classify_event_revision(
                    previous_relation_input,
                    current_relation_input,
                )
                revision_classifications.append(
                    {
                        "dedup_cluster": dedup_key,
                        "event_cluster_id": cluster["event_cluster_id"],
                        **revision_relation,
                    }
                )
                revision_payload = {
                    "entity_refs": entity_refs,
                    "event_type": event_type,
                    "key_points": key_points,
                    "materiality": "unknown",
                    "content_materiality": materiality_candidate[
                        "content_materiality"
                    ],
                    "materiality_contract_version": materiality_candidate[
                        "contract_version"
                    ],
                    "source_refs": source_refs,
                    "verification_state": "verified",
                }
                revision_digest = _digest(revision_payload)
                if latest and (
                    latest["revision_digest"] == revision_digest
                    or revision_relation["creates_new_revision"] is False
                ):
                    revision = latest
                else:
                    available_at = max(str(ref["available_at"]) for ref in source_refs)
                    expiry_candidates = [
                        str(member["content_expires_at"]) for member in members
                    ]
                    available_value = _aware(available_at, "revision.available_at")
                    hot_expiry_value = min(
                        [
                            _aware(value, "content_expires_at")
                            for value in expiry_candidates
                        ]
                        + [available_value + timedelta(days=7)]
                    )
                    hot_expiry = max(available_value, hot_expiry_value).isoformat(
                        timespec="seconds"
                    )
                    revision_no = int(latest["revision_no"]) + 1 if latest else 1
                    revision_id = f"event-revision:{_digest({'cluster': cluster['event_cluster_id'], 'revision': revision_digest})}"
                    revision = create_event_revision(
                        conn,
                        {
                            "event_revision_id": revision_id,
                            "event_cluster_id": cluster["event_cluster_id"],
                            "revision_no": revision_no,
                            "revision_digest": revision_digest,
                            "content_evidence_digest": _digest(
                                {"key_points": key_points, "source_refs": source_refs}
                            ),
                            "content_cutoff": analysis_cutoff,
                            "event_type": event_type,
                            "verification_state": "verified",
                            "materiality": "unknown",
                            "content_materiality": materiality_candidate[
                                "content_materiality"
                            ],
                            "materiality_contract_version": materiality_candidate[
                                "contract_version"
                            ],
                            "key_points": key_points,
                            "short_excerpt": "",
                            "source_refs": source_refs,
                            "price_reaction": {},
                            "supersedes_revision_id": latest["event_revision_id"] if latest else None,
                            "available_at": available_at,
                            "hot_content_expires_at": hot_expiry,
                            "sealed_at": sealed_at,
                            "created_at": sealed_at,
                        },
                        ensure_schema=False,
                    )
                    promoted_revision_ids.append(str(revision["event_revision_id"]))
            member_ids = sorted(str(item["news_item_id"]) for item in members)
            link_research_items_to_event_revision(
                conn,
                news_item_ids=member_ids,
                event_cluster_id=str(cluster["event_cluster_id"]),
                event_revision_id=str(revision["event_revision_id"]),
                updated_at=sealed_at,
            )
            linked_item_ids.extend(member_ids)
    except Exception:
        conn.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
        conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        raise
    conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
    return {
        "contract_version": EVENT_RECONCILIATION_CONTRACT_VERSION,
        "analysis_cutoff": analysis_cutoff,
        "sealed_at": sealed_at,
        "pending_item_count": len(pending),
        "linked_news_item_ids": sorted(set(linked_item_ids)),
        "promoted_event_revision_ids": sorted(set(promoted_revision_ids)),
        "revision_classifications": revision_classifications,
        "unresolved": unresolved,
        "canonical_event_evidence_writes": 0,
        "raw_article_bodies_retained": 0,
        "zero_model_calls": 0,
    }
