from __future__ import annotations

"""Authorize, revalidate, render, and persist one shared canonical model answer."""

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from core.db import db
from core.line_model_contract import MODEL_FACT_PACKET_VERSION
from core.line_model_release_config import candidate_delivery_decision
from core.line_model_validation import (
    MODEL_ANALYSIS_VALIDATOR_VERSION,
    validate_model_analysis_v2,
)
from repository.single_track_v3_repository import (
    canonical_analysis_artifact,
    canonical_model_answer_extension,
    seal_canonical_model_answer_extension,
)
from services.canonical_model_candidate_service import CANONICAL_CANDIDATE_VERSION
from services.line_model_candidate_reply_service import (
    CANDIDATE_REPLY_RENDERER_VERSION,
    render_validated_candidate_reply_preview,
)


TPE = ZoneInfo("Asia/Taipei")
CANONICAL_MODEL_ANSWER_FINALIZER_VERSION = "CanonicalModelAnswerFinalizerV1"


class CanonicalModelAnswerFinalizationError(ValueError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _sha256_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise CanonicalModelAnswerFinalizationError(
            f"{field} must be a lowercase SHA-256 digest",
            reason_code=f"{field}_invalid",
        )
    return normalized


def _target_identity(packet: Mapping[str, Any]) -> tuple[str, str]:
    entities = [
        item
        for item in packet.get("target_entities") or []
        if isinstance(item, Mapping)
    ]
    if not entities:
        raise CanonicalModelAnswerFinalizationError(
            "canonical packet target entity is unavailable",
            reason_code="candidate_target_entity_missing",
        )
    primary = entities[0]
    code = str(primary.get("code") or "").strip()
    name = str(primary.get("name") or primary.get("trading_name") or code).strip()
    if len(code) != 4 or not code.isdigit():
        raise CanonicalModelAnswerFinalizationError(
            "canonical packet target code is invalid",
            reason_code="candidate_target_entity_invalid",
        )
    return code, name


def finalize_authorized_canonical_model_answer(
    submission: Mapping[str, Any],
    *,
    cohort_key: str,
    decision_provider: Callable[[str], Mapping[str, Any]] = candidate_delivery_decision,
    connection_factory: Callable[[], sqlite3.Connection] = db,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Persist only an independently revalidated answer from an authorized canary."""

    decision = dict(decision_provider(str(cohort_key or "")) or {})
    if decision.get("authorized") is not True or decision.get("selected") is not True:
        reason = str((decision.get("reason_codes") or ["release_not_authorized"])[0])
        raise CanonicalModelAnswerFinalizationError(
            "canonical model answer finalization is not authorized",
            reason_code=reason,
        )
    packet = submission.get("compacted_packet")
    if not isinstance(packet, Mapping) or packet.get("contract_version") != MODEL_FACT_PACKET_VERSION:
        raise CanonicalModelAnswerFinalizationError(
            "canonical compacted packet is invalid",
            reason_code="candidate_packet_invalid",
        )
    packet = dict(packet)
    identity = packet.get("artifact_identity")
    if not isinstance(identity, Mapping):
        raise CanonicalModelAnswerFinalizationError(
            "canonical artifact identity is missing",
            reason_code="candidate_canonical_artifact_binding_missing",
        )
    analysis_id = str(identity.get("analysis_id") or "").strip()
    snapshot_id = str(identity.get("snapshot_id") or "").strip()
    base_answer_hash = _digest(
        identity.get("canonical_answer_text_hash"),
        "base_answer_text_hash",
    )
    compacted_packet_sha256 = _sha256_text(_canonical_json(packet))
    if compacted_packet_sha256 != _digest(
        submission.get("compacted_packet_sha256"),
        "compacted_packet_sha256",
    ):
        raise CanonicalModelAnswerFinalizationError(
            "compacted packet digest mismatch",
            reason_code="candidate_compacted_packet_digest_mismatch",
        )
    packet_digest = _digest(submission.get("packet_digest"), "packet_digest")
    if packet_digest != str(packet.get("packet_digest") or ""):
        raise CanonicalModelAnswerFinalizationError(
            "packet digest is not bound to the compacted packet",
            reason_code="candidate_packet_digest_mismatch",
        )
    if str(submission.get("candidate_version") or "") != CANONICAL_CANDIDATE_VERSION:
        raise CanonicalModelAnswerFinalizationError(
            "candidate version is not release-bound",
            reason_code="candidate_version_invalid",
        )
    validated_output = str(submission.get("validated_model_output") or "")
    if not validated_output or _sha256_text(validated_output) != _digest(
        submission.get("model_output_sha256"),
        "model_output_sha256",
    ):
        raise CanonicalModelAnswerFinalizationError(
            "validated model output digest mismatch",
            reason_code="candidate_model_output_digest_mismatch",
        )
    validator = validate_model_analysis_v2(validated_output, packet)
    if (
        not validator.passed
        or validator.ungrounded_claim_count != 0
        or validator.referee_override_count != 0
    ):
        reason = str((validator.reason_codes or ("candidate_validator_not_passed",))[0])
        raise CanonicalModelAnswerFinalizationError(
            "candidate output failed authoritative revalidation",
            reason_code=reason,
        )
    validated_analysis = dict(validator.analysis or {})
    code, name = _target_identity(packet)
    preview = render_validated_candidate_reply_preview(
        {
            "validator_result": "pass",
            "compacted_packet": packet,
            "explanation_blocks": list(validated_analysis.get("explanation_blocks") or []),
            "rendered_blocks": list(validator.rendered_blocks),
            "used_event_ids": list(validated_analysis.get("used_event_ids") or []),
        },
        stock_code=code,
        stock_name=name,
    )
    release_source_digest = _digest(
        decision.get("release_source_digest"),
        "release_source_digest",
    )
    authorization_id = str(decision.get("authorization_id") or "").strip()
    if not authorization_id:
        raise CanonicalModelAnswerFinalizationError(
            "release authorization identity is unavailable",
            reason_code="release_authorization_id_missing",
        )
    extension = {
        "analysis_id": analysis_id,
        "base_answer_text_hash": base_answer_hash,
        "packet_digest": packet_digest,
        "compacted_packet_sha256": compacted_packet_sha256,
        "model_id": str(submission.get("model_id") or ""),
        "model_digest": _digest(submission.get("model_digest"), "model_digest"),
        "candidate_version": CANONICAL_CANDIDATE_VERSION,
        "generation_schema_version": str(submission.get("generation_schema_version") or ""),
        "generation_schema_sha256": _digest(
            submission.get("generation_schema_sha256"),
            "generation_schema_sha256",
        ),
        "generation_prompt_sha256": _digest(
            submission.get("generation_prompt_sha256"),
            "generation_prompt_sha256",
        ),
        "validator_version": MODEL_ANALYSIS_VALIDATOR_VERSION,
        "renderer_version": CANDIDATE_REPLY_RENDERER_VERSION,
        "explanation_blocks": list(validated_analysis.get("explanation_blocks") or []),
        "used_event_ids": list(validated_analysis.get("used_event_ids") or []),
        "research_limitations": list(validated_analysis.get("research_limitations") or []),
        "canonical_answer_text": str(preview["text"]),
        "canonical_answer_text_hash": str(preview["sha256"]),
        "authorization_id": authorization_id,
        "release_source_digest": release_source_digest,
        "created_at": str(created_at or datetime.now(TPE).isoformat(timespec="seconds")),
    }

    connection = connection_factory()
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        artifact = canonical_analysis_artifact(connection, analysis_id)
        if artifact is None:
            raise CanonicalModelAnswerFinalizationError(
                "canonical analysis artifact is unavailable",
                reason_code="candidate_canonical_artifact_not_found",
            )
        if str(artifact.get("snapshot_id") or "") != snapshot_id:
            raise CanonicalModelAnswerFinalizationError(
                "candidate snapshot identity mismatch",
                reason_code="candidate_snapshot_identity_mismatch",
            )
        if str(artifact.get("canonical_answer_text_hash") or "") != base_answer_hash:
            raise CanonicalModelAnswerFinalizationError(
                "candidate base answer identity mismatch",
                reason_code="candidate_base_answer_identity_mismatch",
            )
        existing = canonical_model_answer_extension(connection, analysis_id)
        try:
            saved = seal_canonical_model_answer_extension(connection, extension)
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            saved = seal_canonical_model_answer_extension(connection, extension)
        return {
            "ok": True,
            "finalizer_version": CANONICAL_MODEL_ANSWER_FINALIZER_VERSION,
            "analysis_id": analysis_id,
            "snapshot_id": snapshot_id,
            "canonical_answer_text": saved["canonical_answer_text"],
            "canonical_answer_text_hash": saved["canonical_answer_text_hash"],
            "model_digest": saved["model_digest"],
            "authorization_id": saved["authorization_id"],
            "reused": existing is not None,
            "raw_model_output_persisted": False,
        }
    finally:
        connection.close()
