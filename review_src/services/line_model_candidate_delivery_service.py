from __future__ import annotations

"""Final fail-closed selection between a stable reply and one canonical candidate."""

import hashlib
from typing import Any, Callable

from core.line_model_release_config import candidate_delivery_decision
from services.line_model_candidate_reply_service import (
    CandidateReplyRenderError,
    render_validated_candidate_reply_preview,
)


CANDIDATE_DELIVERY_VERSION = "LineModelCandidateDeliveryV1"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def select_canonical_candidate_reply(
    *,
    stable_reply: str,
    candidate_result: dict[str, Any] | None,
    stock_code: str,
    stock_name: str,
    cohort_key: str,
    completed_before_work_stop: bool,
    canonical_model_answer: dict[str, Any] | None = None,
    decision_provider: Callable[[str], dict[str, Any]] = candidate_delivery_decision,
) -> dict[str, Any]:
    """Return candidate text only when release and candidate gates all pass."""

    stable = str(stable_reply or "")
    if not stable:
        raise ValueError("stable_reply is required for immediate rollback")
    decision = dict(decision_provider(str(cohort_key or "")) or {})
    selected = decision.get("selected") is True
    reason = str((decision.get("reason_codes") or ["release_not_authorized"])[0])
    candidate = candidate_result if isinstance(candidate_result, dict) else {}
    finalized = canonical_model_answer if isinstance(canonical_model_answer, dict) else {}
    preview: dict[str, Any] | None = None
    if selected and not completed_before_work_stop:
        reason = "candidate_missed_work_stop_budget"
    elif selected and candidate.get("validator_result") != "pass":
        reason = "candidate_validator_not_passed"
    elif selected and candidate.get("candidate_can_override_referee") is not False:
        reason = "candidate_referee_override_contract_invalid"
    elif selected:
        packet = candidate.get("compacted_packet")
        artifact_identity = (
            packet.get("artifact_identity") if isinstance(packet, dict) else None
        )
        if (
            not isinstance(artifact_identity, dict)
            or not str(artifact_identity.get("analysis_id") or "")
            or not str(artifact_identity.get("canonical_answer_text_hash") or "")
        ):
            reason = "candidate_canonical_artifact_binding_missing"
        else:
            try:
                preview = render_validated_candidate_reply_preview(
                    candidate,
                    stock_code=str(stock_code),
                    stock_name=str(stock_name),
                )
                finalized_text = str(finalized.get("canonical_answer_text") or "")
                finalized_hash = str(finalized.get("canonical_answer_text_hash") or "")
                if not finalized_text or not finalized_hash:
                    reason = "candidate_shared_model_answer_missing"
                    preview = None
                elif finalized.get("analysis_id") != artifact_identity.get("analysis_id"):
                    reason = "candidate_shared_model_answer_identity_mismatch"
                    preview = None
                elif _sha256(finalized_text) != finalized_hash or preview.get("sha256") != finalized_hash:
                    reason = "candidate_web_line_canonical_hash_mismatch"
                    preview = None
            except CandidateReplyRenderError as exc:
                reason = exc.reason_code

    candidate_delivered = bool(selected and completed_before_work_stop and preview)
    reply = (
        str(finalized["canonical_answer_text"])
        if candidate_delivered and preview
        else stable
    )
    return {
        "delivery_version": CANDIDATE_DELIVERY_VERSION,
        "text": reply,
        "text_sha256": _sha256(reply),
        "delivery_path": "canonical_candidate" if candidate_delivered else "stable_fallback",
        "candidate_authorized": decision.get("authorized") is True,
        "candidate_selected": selected,
        "candidate_delivered": candidate_delivered,
        "candidate_reason": "delivered" if candidate_delivered else reason,
        "canary_percentage": decision.get("configured_canary_percentage"),
        "cohort_bucket": decision.get("cohort_bucket"),
        "authorization_id": decision.get("authorization_id"),
        "stable_reply_sha256": _sha256(stable),
        "candidate_reply_sha256": preview.get("sha256") if preview else None,
        "canonical_model_answer_persisted": bool(
            candidate_delivered and finalized.get("raw_model_output_persisted") is False
        ),
        "analysis_id": (
            ((candidate.get("compacted_packet") or {}).get("artifact_identity") or {}).get(
                "analysis_id"
            )
            if isinstance(candidate.get("compacted_packet"), dict)
            else None
        ),
        "raw_candidate_persisted": False,
    }
