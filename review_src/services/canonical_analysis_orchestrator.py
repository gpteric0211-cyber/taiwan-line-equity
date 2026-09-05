from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from analysis.technical_ensemble_v1 import TECHNICAL_FORMULA_VERSION_V1
from core.db import db
from core.market_timing import parse_market_timestamp
from core.single_track_v3_schema import ensure_single_track_v3_schema
from repository.single_track_v3_repository import (
    canonical_analysis_artifact,
    canonical_answer_hash,
    seal_canonical_analysis_artifact,
    technical_components_for_snapshot,
    upsert_evidence_facts,
)
from services.bot_market_data_service import (
    CANONICAL_CLOSE_BATCH_CONTRACT_VERSION,
    build_canonical_close_batch_snapshot,
)
from services.event_safety_scan_service import (
    SOURCE_POLICY_VERSION,
    build_event_safety_scan,
    persist_event_safety_scan,
)
from services.expert_response_renderer import (
    EXPERT_RENDERER_VERSION,
    render_expert_response,
)


TPE = ZoneInfo("Asia/Taipei")
CANONICAL_ANALYSIS_ORCHESTRATOR_VERSION = "CanonicalAnalysisOrchestratorV1"
CANONICAL_ARTIFACT_VERSION = "CanonicalAnalysisArtifactV1"
WEIGHT_VERSION = "CandidateWeightV1"
PROMPT_VERSION = "DeterministicCanonicalRendererPromptV1"
VALIDATOR_VERSION = "CanonicalArtifactStructuralValidatorV1"
RENDERER_VERSION = EXPERT_RENDERER_VERSION
ENTITY_REGISTRY_VERSION = "StockEntityRegistryV1"
CONVERSATION_PROJECTION_VERSION = "ConversationProjectionV1"
RESPONSE_STYLE_VERSION = "ResponseStyleV1"
SUPPORTED_PROFILES = {"focused", "comprehensive"}
CANONICAL_SECTION_KEYS = {
    "technical": "technical",
    "support_resistance": "support_pressure",
    "valuation": "valuation",
    "institutional_context": "institutional_context",
    "global_market_context": "global_market_context",
    "taifex_night_context": "taifex_night_context",
    "trading_state": "trading_state",
    "recommendation_safety": "recommendation_safety",
    "decision_audit": "decision_audit",
    "advisory": "advisory",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _stable_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_digest(value)}"


def _normalized_timestamp(value: str | None, field: str, *, default: datetime | None = None) -> str:
    parsed = parse_market_timestamp(value) if value else default
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be an offset-aware ISO-8601 timestamp")
    return parsed.astimezone(TPE).isoformat(timespec="seconds")


def _context_digest(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return text
    return _digest({"conversation_context": text})


def _event_rows(snapshot: Mapping[str, Any], code: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    contexts = (
        ("official_event_context", "canonical_official"),
        ("external_event_context", "licensed_secondary"),
        ("news_radar_context", "news_radar"),
    )
    for context_key, source_class in contexts:
        context = snapshot.get(context_key)
        if not isinstance(context, Mapping):
            continue
        for source in context.get("events") or []:
            if not isinstance(source, Mapping):
                continue
            row = dict(source)
            available_at = row.get("available_at") or row.get("retrieved_at")
            if not available_at:
                continue
            row.update(
                {
                    "code": code,
                    "entity_refs": [code],
                    "source_class": source_class,
                    "available_at": available_at,
                    "retrieved_at": row.get("retrieved_at") or available_at,
                    "title": row.get("title") or row.get("subject"),
                    "source_url": row.get("source_url") or row.get("url") or "",
                    "direction": (
                        row.get("potential_direction")
                        if source_class == "news_radar"
                        else row.get("direction")
                    )
                    or "unknown",
                }
            )
            rows.append(row)
    return rows


def _source_results(snapshot: Mapping[str, Any]) -> dict[str, str]:
    official = snapshot.get("official_event_context") or {}
    external = snapshot.get("external_event_context") or {}
    radar = snapshot.get("news_radar_context") or {}
    global_context = snapshot.get("global_market_context") or {}
    night = snapshot.get("taifex_night_context") or {}
    return {
        "official_company": "ok" if official.get("available") else "unavailable",
        "taiwan_policy": "missing",
        "us_policy_geopolitics": "missing",
        "company_industry_news": (
            "ok" if external.get("available") and radar.get("available") else "unavailable"
        ),
        "related_overseas_price_reaction": (
            "ok" if global_context.get("available") else "unavailable"
        ),
        "us_market_taiwan_night": (
            "ok" if global_context.get("available") and night.get("available") else "unavailable"
        ),
        "dilution_valuation_risk": "missing",
    }


def _combined_source_results(snapshots: list[Mapping[str, Any]]) -> dict[str, str]:
    if not snapshots:
        return {}
    rows = [_source_results(snapshot) for snapshot in snapshots]
    scopes = set().union(*(row.keys() for row in rows))
    return {
        scope: "ok" if all(row.get(scope) == "ok" for row in rows) else "unavailable"
        for scope in scopes
    }


def _canonical_sections(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Retain bounded, already-computed canonical sections without recalculation."""

    sections = {
        target: dict(snapshot.get(source) or {})
        for target, source in CANONICAL_SECTION_KEYS.items()
        if isinstance(snapshot.get(source), Mapping)
    }
    sections["price_volume"] = {
        key: dict(snapshot.get(key) or {})
        for key in ("multi_day_score", "flow_summary", "scoped_price_volume_display")
        if isinstance(snapshot.get(key), Mapping)
    }
    return sections


def _previous_artifact(
    conn: sqlite3.Connection,
    *,
    code: str,
    analysis_cutoff: str,
) -> dict[str, str] | None:
    rows = conn.execute(
        """
        SELECT analysis_id,analysis_cutoff,canonical_payload_json
        FROM canonical_analysis_artifact
        WHERE analysis_cutoff<? AND validity IN ('valid','partial')
        ORDER BY analysis_cutoff DESC
        LIMIT 100
        """,
        (analysis_cutoff,),
    ).fetchall()
    for source in rows:
        try:
            payload = json.loads(str(source[2] or "{}"))
        except (TypeError, ValueError):
            continue
        if str(payload.get("code") or "").zfill(4) == code:
            return {"analysis_id": str(source[0]), "analysis_cutoff": str(source[1])}
    return None


def _technical_ensemble_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "version": "TechnicalEnsembleV1",
            "formula_version": TECHNICAL_FORMULA_VERSION_V1,
            "status": "unavailable",
            "availability_reason": "scheduled_candidate_components_not_materialized",
            "overall_score": None,
            "family_scores": {},
            "component_count": 0,
        }
    values = {
        str(row.get("component_key")): row.get("value")
        for row in rows
        if row.get("value") is not None
    }
    family_scores = {
        key: values.get(key)
        for key in (
            "trend_score",
            "momentum_score",
            "volume_score",
            "volatility_score",
            "psychology_score",
        )
        if values.get(key) is not None
    }
    return {
        "version": "TechnicalEnsembleV1",
        "formula_version": TECHNICAL_FORMULA_VERSION_V1,
        "status": "ok" if len(rows) >= 62 and len(family_scores) == 5 else "partial",
        "availability_reason": None if len(rows) >= 62 else "incomplete_component_coverage",
        "overall_score": values.get("ensemble_score"),
        "family_scores": family_scores,
        "component_count": len(rows),
        "input_snapshot_digest": rows[0].get("input_snapshot_digest"),
    }


def _close_fact(
    snapshot: Mapping[str, Any],
    *,
    code: str,
    snapshot_id: str,
    cutoff: str,
    recorded_at: str,
) -> dict[str, Any] | None:
    ohlcv = snapshot.get("ohlcv") or {}
    close = ohlcv.get("close")
    trade_date = str(snapshot.get("trade_date") or ohlcv.get("date") or "")
    if close is None or not trade_date or not ohlcv.get("official_trusted"):
        return None
    as_of = f"{trade_date}T13:30:00+08:00"
    identity = {"snapshot_id": snapshot_id, "entity": code, "field": "close"}
    return {
        "fact_id": _stable_id("fact", identity),
        "entity": code,
        "field": "close",
        "value": close,
        "unit_currency": "TWD",
        "period": "daily",
        "trade_date": trade_date,
        "as_of": as_of,
        # The database snapshot proves the value was known no later than this cutoff.
        # Using the conservative upper bound avoids inventing an earlier ingest time.
        "available_at": cutoff,
        "source_market_timestamp": as_of,
        "session": "close",
        "adjustment_basis": "official_daily_close",
        "authority_tier": "canonical_official",
        "quality": "ok",
        "availability_reason": "observed_in_sealed_snapshot_at_cutoff",
        "use_scope": "referee_and_renderer",
        "snapshot_id": snapshot_id,
        "provenance": {"source": ohlcv.get("source"), "source_quality": ohlcv.get("source_quality")},
        "formula_source_version": CANONICAL_CLOSE_BATCH_CONTRACT_VERSION,
        "recorded_at": recorded_at,
    }


def _response(saved: Mapping[str, Any], *, delivery_channel: str, reused: bool) -> dict[str, Any]:
    payload = dict(saved.get("canonical_payload") or {})
    base_text = str(saved.get("canonical_answer_text") or "")
    base_hash = str(saved.get("canonical_answer_text_hash") or "")
    model_answer = saved.get("canonical_model_answer")
    finalized = isinstance(model_answer, Mapping)
    selected_text = str(model_answer.get("canonical_answer_text") or "") if finalized else base_text
    selected_hash = (
        str(model_answer.get("canonical_answer_text_hash") or "")
        if finalized
        else base_hash
    )
    if finalized:
        payload["canonical_answer_text"] = selected_text
        payload["canonical_answer_text_hash"] = selected_hash
        payload["canonical_model_answer"] = {
            key: model_answer.get(key)
            for key in (
                "model_id",
                "model_digest",
                "candidate_version",
                "generation_schema_version",
                "validator_version",
                "renderer_version",
                "explanation_blocks",
                "used_event_ids",
                "research_limitations",
                "authorization_id",
                "release_source_digest",
                "created_at",
            )
        }
    return {
        "ok": str(saved.get("validity")) != "invalid",
        "artifact_version": CANONICAL_ARTIFACT_VERSION,
        "delivery_channel": delivery_channel,
        "reused": reused,
        "analysis_id": saved.get("analysis_id"),
        "snapshot_id": saved.get("snapshot_id"),
        "analysis_cutoff": saved.get("analysis_cutoff"),
        "validity": saved.get("validity"),
        "canonical_answer_text": selected_text,
        "canonical_answer_text_hash": selected_hash,
        "base_canonical_answer_text": base_text,
        "base_canonical_answer_text_hash": base_hash,
        "model_answer_finalized": finalized,
        "model_answer_authorization_id": (
            model_answer.get("authorization_id") if finalized else None
        ),
        "model_answer_release_source_digest": (
            model_answer.get("release_source_digest") if finalized else None
        ),
        "event_ids": list(saved.get("event_ids") or []),
        "evidence_ids": list(saved.get("fact_ids") or []),
        "omissions": list(saved.get("omissions") or []),
        "conflicts": list(saved.get("conflicts") or []),
        "analysis": payload,
    }


def run_canonical_analysis(
    *,
    code: str,
    delivery_channel: str,
    trade_date: str | None = None,
    analysis_cutoff: str | None = None,
    request_received_at: str | None = None,
    conversation_context_digest: str | None = None,
    profile: str = "focused",
    entity_resolution: Mapping[str, Any] | None = None,
    conversation_projection: Mapping[str, Any] | None = None,
    connection_factory: Callable[[], sqlite3.Connection] = db,
    snapshot_builder: Callable[..., dict[str, Any]] = build_canonical_close_batch_snapshot,
) -> dict[str, Any]:
    """Generate once and persist the shared artifact consumed by Web and LINE."""

    normalized_code = str(code or "").strip()
    if not re.fullmatch(r"\d{4}", normalized_code):
        raise ValueError("code must be exactly four digits")
    normalized_channel = str(delivery_channel or "").strip().lower()
    if normalized_channel not in {"web", "line"}:
        raise ValueError("delivery_channel must be web or line")
    normalized_profile = str(profile or "focused").strip().lower()
    if normalized_profile not in SUPPORTED_PROFILES:
        raise ValueError("profile must be focused or comprehensive")
    now = datetime.now(TPE)
    received = _normalized_timestamp(request_received_at, "request_received_at", default=now)
    cutoff = _normalized_timestamp(analysis_cutoff, "analysis_cutoff", default=parse_market_timestamp(received))
    if parse_market_timestamp(cutoff) > parse_market_timestamp(received):
        raise ValueError("analysis_cutoff cannot be later than request_received_at")
    context_digest = _context_digest(conversation_context_digest)
    resolution_entities = [
        dict(item)
        for item in list((entity_resolution or {}).get("entities") or [])
        if isinstance(item, Mapping) and re.fullmatch(r"\d{4}", str(item.get("code") or ""))
    ]
    if not resolution_entities:
        resolution_entities = [{"code": normalized_code}]
    if str(resolution_entities[0].get("code") or "") != normalized_code:
        raise ValueError("primary resolved entity must match code")
    target_codes = list(dict.fromkeys(str(item["code"]) for item in resolution_entities))
    if len(target_codes) > 4:
        raise ValueError("at most four comparison entities are supported")
    normalized_request = {
        "code": normalized_code,
        "target_codes": target_codes,
        "trade_date": str(trade_date or "") or None,
        "analysis_cutoff": cutoff,
        "context_digest": context_digest,
        "profile": normalized_profile,
        "orchestrator_version": CANONICAL_ANALYSIS_ORCHESTRATOR_VERSION,
    }
    analysis_id = _stable_id("analysis", normalized_request)

    conn = connection_factory()
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_single_track_v3_schema(conn)
        existing = canonical_analysis_artifact(conn, analysis_id)
        if existing is not None:
            conn.commit()
            return _response(existing, delivery_channel=normalized_channel, reused=True)

        previous = _previous_artifact(conn, code=normalized_code, analysis_cutoff=cutoff)
        entity_snapshots: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for entity in resolution_entities:
            entity_snapshot = snapshot_builder(
                str(entity["code"]),
                trade_date=trade_date,
                include_levels=True,
                level_limit=100,
                analysis_mode="close_batch",
                allow_live_quote_fetch=False,
                analysis_cutoff=cutoff,
            )
            entity_snapshots.append((entity, entity_snapshot))
        snapshot = entity_snapshots[0][1]
        snapshot_package: Any = (
            snapshot
            if len(entity_snapshots) == 1
            else {
                "comparison_entities": [
                    {"entity": entity, "snapshot": entity_snapshot}
                    for entity, entity_snapshot in entity_snapshots
                ]
            }
        )
        snapshot_digest = _digest(snapshot_package)
        snapshot_id = _stable_id(
            "snapshot",
            {"code": normalized_code, "cutoff": cutoff, "digest": snapshot_digest},
        )
        selected_date = str(snapshot.get("trade_date") or "")
        technical_rows_by_code: dict[str, list[dict[str, Any]]] = {}
        technical_ensembles: dict[str, dict[str, Any]] = {}
        for entity, entity_snapshot in entity_snapshots:
            entity_code = str(entity["code"])
            entity_date = str(entity_snapshot.get("trade_date") or "")
            rows = (
                technical_components_for_snapshot(
                    conn,
                    entity_code,
                    entity_date,
                    formula_version=TECHNICAL_FORMULA_VERSION_V1,
                )
                if entity_date
                else []
            )
            technical_rows_by_code[entity_code] = rows
            technical_ensembles[entity_code] = _technical_ensemble_payload(rows)
        technical_rows = technical_rows_by_code[normalized_code]
        technical_ensemble = technical_ensembles[normalized_code]
        combined_events = [
            event
            for entity, entity_snapshot in entity_snapshots
            for event in _event_rows(entity_snapshot, str(entity["code"]))
        ]
        scan = build_event_safety_scan(
            entity_refs=target_codes,
            analysis_cutoff=cutoff,
            events=combined_events,
            source_results=_combined_source_results([item[1] for item in entity_snapshots]),
            previous_artifact_cutoff=previous.get("analysis_cutoff") if previous else None,
            request_received_at=received,
        )
        persist_event_safety_scan(
            conn,
            scan,
            previous_analysis_id=previous.get("analysis_id") if previous else None,
        )
        sealed_at = datetime.now(TPE).isoformat(timespec="seconds")
        facts = [
            fact
            for entity, entity_snapshot in entity_snapshots
            for fact in [
                _close_fact(
                    entity_snapshot,
                    code=str(entity["code"]),
                    snapshot_id=snapshot_id,
                    cutoff=cutoff,
                    recorded_at=sealed_at,
                )
            ]
            if fact is not None
        ]
        upsert_evidence_facts(conn, facts)
        fact_claim_ids = {
            str(fact["fact_id"]): "official-close-claim"
            for fact in facts
        }
        event_use_scopes = {
            str(event["event_id"]): (
                "material_event"
                if event.get("verification_state") == "verified" and event.get("materiality") == "material"
                else "background"
                if event.get("verification_state") == "verified"
                else "radar_discovery"
            )
            for event in scan.get("events") or []
        }
        referee = snapshot.get("referee") or {}
        support_coverage = dict(referee.get("component_coverage") or {})
        if not support_coverage:
            support_coverage = {
                "support": isinstance(referee.get("support_zone"), dict),
                "resistance": isinstance(referee.get("resistance_zone"), dict),
            }
        omissions = list(scan.get("omissions") or [])
        omissions.extend(
            {"scope": scope, "reason": "event_scan_incomplete"}
            for scope in scan.get("incomplete_scopes") or []
        )
        omissions.extend(list(referee.get("omissions") or []))
        analysis_status = snapshot.get("analysis_status") or {}
        all_analysis_statuses = [
            entity_snapshot.get("analysis_status") or {}
            for _entity, entity_snapshot in entity_snapshots
        ]
        validity = (
            "valid"
            if all(item.get("status") == "ready" for item in all_analysis_statuses)
            and scan.get("scan_state") in {"verified_none", "verified_material"}
            else "invalid"
            if not facts
            and not any(item.get("status") in {"ready", "blocked"} for item in all_analysis_statuses)
            else "partial"
        )
        evidence_ids_by_entity = {
            code: sorted(
                str(fact["fact_id"])
                for fact in facts
                if str(fact.get("entity") or "") == code
            )
            for code in target_codes
        }
        rendered = render_expert_response(
            entity_snapshots=entity_snapshots,
            scan=scan,
            evidence_ids_by_entity=evidence_ids_by_entity,
            profile=normalized_profile,
            conversation_projection=conversation_projection,
        )
        answer = str(rendered["text"])
        answer_hash = canonical_answer_hash(answer)
        component_snapshot_ids = []
        for entity, entity_snapshot in entity_snapshots:
            entity_code = str(entity["code"])
            rows = technical_rows_by_code[entity_code]
            if rows:
                component_snapshot_ids.append(
                    _stable_id(
                        "technical",
                        {
                            "code": entity_code,
                            "trade_date": entity_snapshot.get("trade_date"),
                            "formula_version": TECHNICAL_FORMULA_VERSION_V1,
                            "input_snapshot_digest": rows[0].get("input_snapshot_digest"),
                        },
                    )
                )
        entity_analysis_payloads = [
            {
                "entity": entity,
                "trade_date": entity_snapshot.get("trade_date"),
                "analysis_status": entity_snapshot.get("analysis_status"),
                "referee": entity_snapshot.get("referee"),
                "ohlcv": entity_snapshot.get("ohlcv"),
                "technical_ensemble": technical_ensembles[str(entity["code"])],
                "canonical_sections": _canonical_sections(entity_snapshot),
            }
            for entity, entity_snapshot in entity_snapshots
        ]
        canonical_payload = {
            "analysis_id": analysis_id,
            "snapshot_id": snapshot_id,
            "code": normalized_code,
            "stock": snapshot.get("stock"),
            "trade_date": selected_date or None,
            "analysis_cutoff": cutoff,
            "profile": normalized_profile,
            "context_digest": context_digest,
            "entity_resolution": dict(entity_resolution or {}),
            "target_entities": resolution_entities,
            "comparison_set": resolution_entities if len(resolution_entities) > 1 else [],
            "conversation_projection": dict(conversation_projection or {}),
            "main_conclusion": (
                referee.get("main_status")
                if len(entity_snapshots) == 1
                else "各標的沿用各自唯一裁判結論"
            ),
            "analysis_status": analysis_status,
            "factor_scores": {
                "technical_ensemble": technical_ensemble,
                "by_entity": {
                    code: {"technical_ensemble": technical_ensembles[code]}
                    for code in target_codes
                },
                "event_direction_score": (
                    scan.get("event_direction_score")
                    if scan.get("scan_state") != "scan_incomplete"
                    else None
                ),
            },
            "technical_ensemble": technical_ensemble,
            "technical_ensembles": technical_ensembles,
            "entity_analyses": entity_analysis_payloads,
            "referee": referee,
            "ohlcv": snapshot.get("ohlcv"),
            "event_scan": {
                key: scan.get(key)
                for key in (
                    "scan_id",
                    "scan_state",
                    "event_watermark",
                    "source_policy_version",
                    "coverage",
                    "high_confidence_allowed",
                    "event_direction_score",
                    "invalidation_independent_of_direction",
                )
            },
            "event_ids": sorted(event_use_scopes),
            "evidence_ids": sorted(fact_claim_ids),
            "omissions": omissions,
            "conflicts": list(scan.get("conflicts") or []),
            "answer_claims": {
                "official_closes": sorted(fact_claim_ids),
            },
            "explanation_blocks": list(rendered["explanation_blocks"]),
            "canonical_answer_text": answer,
            "canonical_answer_text_hash": answer_hash,
        }
        coverage = {
            "market": "ok" if facts else "unavailable",
            "referee": analysis_status.get("status"),
            "support_resistance": support_coverage,
            "technical_ensemble": technical_ensemble.get("status"),
            "event_scan": scan.get("scan_state"),
        }
        artifact = {
            "analysis_id": analysis_id,
            "snapshot_id": snapshot_id,
            "snapshot_digest": snapshot_digest,
            "request_received_at": received,
            "analysis_cutoff": cutoff,
            "snapshot_sealed_at": sealed_at,
            "context_digest": context_digest,
            "component_snapshot_ids": component_snapshot_ids,
            "event_watermark": scan.get("event_watermark"),
            "source_policy_version": SOURCE_POLICY_VERSION,
            "weight_version": WEIGHT_VERSION,
            "formula_version": TECHNICAL_FORMULA_VERSION_V1,
            "referee_version": str(referee.get("version") or "unavailable"),
            "model_digest": None,
            "prompt_version": PROMPT_VERSION,
            "validator_version": VALIDATOR_VERSION,
            "renderer_version": RENDERER_VERSION,
            "entity_registry_version": ENTITY_REGISTRY_VERSION,
            "conversation_projection_version": CONVERSATION_PROJECTION_VERSION,
            "response_style_version": RESPONSE_STYLE_VERSION,
            "coverage": coverage,
            "omissions": omissions,
            "conflicts": list(scan.get("conflicts") or []),
            "validity": validity,
            "superseded_by_event_ids": [],
            "superseded_reason": None,
            "canonical_payload": canonical_payload,
            "canonical_answer_text": answer,
            "canonical_answer_text_hash": answer_hash,
            "created_at": sealed_at,
        }
        try:
            seal_canonical_analysis_artifact(
                conn,
                artifact,
                fact_claim_ids=fact_claim_ids,
                event_use_scopes=event_use_scopes,
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            existing = canonical_analysis_artifact(conn, analysis_id)
            if existing is None:
                raise
            return _response(existing, delivery_channel=normalized_channel, reused=True)
        saved = canonical_analysis_artifact(conn, analysis_id)
        if saved is None:
            raise RuntimeError("sealed canonical analysis artifact could not be read back")
        return _response(saved, delivery_channel=normalized_channel, reused=False)
    finally:
        conn.close()
