from __future__ import annotations

"""Build one sealed canonical packet envelope for the LINE model runtime."""

import re
import sqlite3
import time
from typing import Any, Callable, Iterable, Mapping

from core.db import db
from repository.single_track_v3_repository import events_available_at_cutoff
from services.canonical_model_packet_service import build_canonical_model_fact_packet_v2
from services.canonical_question_analysis_service import run_canonical_question_analysis


CANONICAL_MODEL_PACKET_ENVELOPE_VERSION = "CanonicalModelPacketEnvelopeV1"
_SCOPE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _scopes(values: Iterable[Any]) -> list[str]:
    normalized = list(dict.fromkeys(str(value or "").strip().lower() for value in values))
    if not normalized or len(normalized) > 12 or any(not _SCOPE.fullmatch(value) for value in normalized):
        raise ValueError("requested_scopes must contain 1-12 normalized scope names")
    return normalized


def build_canonical_question_model_packet(
    *,
    query: str,
    conversation_context: Mapping[str, Any] | None,
    requested_scopes: Iterable[Any],
    profile: str,
    analysis_cutoff: str | None = None,
    trade_date: str | None = None,
    request_received_at: str | None = None,
    artifact_builder: Callable[..., dict[str, Any]] = run_canonical_question_analysis,
    connection_factory: Callable[[], sqlite3.Connection] = db,
    event_reader: Callable[[sqlite3.Connection, str], list[dict[str, Any]]] = (
        events_available_at_cutoff
    ),
) -> dict[str, Any]:
    """Create/reuse the artifact, then project only its cutoff-eligible events."""

    scopes = _scopes(requested_scopes)
    artifact = artifact_builder(
        query=str(query or ""),
        delivery_channel="line",
        conversation_context=dict(conversation_context or {}),
        trade_date=trade_date,
        analysis_cutoff=analysis_cutoff,
        request_received_at=request_received_at,
        profile=str(profile or "focused"),
    )
    if artifact.get("requires_clarification") is True:
        return {
            "contract_version": CANONICAL_MODEL_PACKET_ENVELOPE_VERSION,
            "packet_ready": False,
            "requires_clarification": True,
            "resolution": artifact.get("resolution"),
            "reason_codes": ["canonical_entity_clarification_required"],
            "stage_timings_ms": dict(artifact.get("stage_timings_ms") or {}),
        }
    analysis_id = str(artifact.get("analysis_id") or "")
    cutoff = str(artifact.get("analysis_cutoff") or "")
    canonical_answer_text = str(artifact.get("canonical_answer_text") or "")
    if (
        not analysis_id
        or not cutoff
        or not canonical_answer_text
        or not str(artifact.get("canonical_answer_text_hash") or "")
    ):
        raise ValueError("sealed canonical artifact identity is incomplete")
    event_retrieval_started = time.perf_counter_ns()
    connection = connection_factory()
    try:
        available_events = event_reader(connection, cutoff)
    finally:
        connection.close()
    event_retrieval_ms = round(
        (time.perf_counter_ns() - event_retrieval_started) / 1_000_000,
        3,
    )
    allowed_event_ids = {str(item) for item in artifact.get("event_ids") or []}
    packet_build_started = time.perf_counter_ns()
    packet = build_canonical_model_fact_packet_v2(
        artifact,
        requested_scopes=scopes,
        event_records=(
            event
            for event in available_events
            if str(event.get("event_id") or "") in allowed_event_ids
        ),
    )
    packet_build_ms = round(
        (time.perf_counter_ns() - packet_build_started) / 1_000_000,
        3,
    )
    identity = packet.get("artifact_identity") if isinstance(packet.get("artifact_identity"), dict) else {}
    if identity.get("analysis_id") != analysis_id:
        raise RuntimeError("canonical packet artifact identity mismatch")
    return {
        "contract_version": CANONICAL_MODEL_PACKET_ENVELOPE_VERSION,
        "packet_ready": True,
        "requires_clarification": False,
        "analysis_id": analysis_id,
        "snapshot_id": artifact.get("snapshot_id"),
        "analysis_cutoff": cutoff,
        "canonical_answer_text_hash": artifact.get("canonical_answer_text_hash"),
        "canonical_answer_text": canonical_answer_text,
        "base_canonical_answer_text_hash": artifact.get(
            "base_canonical_answer_text_hash"
        ),
        "base_canonical_answer_text": artifact.get("base_canonical_answer_text"),
        "model_answer_finalized": artifact.get("model_answer_finalized") is True,
        "model_answer_authorization_id": artifact.get("model_answer_authorization_id"),
        "model_answer_release_source_digest": artifact.get(
            "model_answer_release_source_digest"
        ),
        "artifact_validity": artifact.get("validity"),
        "artifact_reused": artifact.get("reused") is True,
        "packet_digest": packet.get("packet_digest"),
        "packet": packet,
        "event_record_count": len(packet.get("events") or []),
        "raw_event_text_outside_packet_returned": False,
        "stage_timings_ms": {
            **dict(artifact.get("stage_timings_ms") or {}),
            "event_retrieval": event_retrieval_ms,
            "packet_build": packet_build_ms,
        },
    }
