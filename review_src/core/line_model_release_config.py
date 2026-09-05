from __future__ import annotations

"""Fail-closed authorization for user-visible LINE model canary delivery."""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.line_bot_config import env_int, env_text
from core.release_source_fingerprint import current_runtime_source_fingerprint


RELEASE_AUTHORIZATION_VERSION = "LineModelReleaseAuthorizationV1"
PROSPECTIVE_EVIDENCE_CONTRACT_VERSION = "ProspectivePredictionEvidenceContractV1"
LEGAL_REVIEW_PACKET_VERSION = "LegalReviewPacketV1"
LEGAL_APPROVAL_STATE = "external_gate_approved_with_conditions"
RELEASE_ROLLOUT_VALUES = frozenset({"off", "shadow", "canary"})
ALLOWED_CANARY_PERCENTAGES = frozenset({5, 10, 25, 50, 100})
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RELEASE_RUNTIME_SOURCE_FINGERPRINT = current_runtime_source_fingerprint()
_ZERO_VIOLATION_FIELDS = (
    "protected_analysis_diff",
    "ungrounded_numeric_or_date_claims",
    "referee_overrides",
    "expired_or_reused_reply_tokens",
    "wrong_entity_resolution",
    "unique_alias_unnecessary_confirmation",
    "web_line_canonical_mismatch",
    "raw_image_or_full_ocr_persistence",
)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_digest(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def configured_release_rollout() -> str:
    configured = env_text("LINE_MODEL_V2_ROLLOUT", "off").strip().lower()
    return configured if configured in RELEASE_ROLLOUT_VALUES else "off"


def configured_canary_percentage() -> int | None:
    configured = env_int("LINE_MODEL_V2_CANARY_PERCENT", 0, minimum=0, maximum=100)
    return configured if configured in ALLOWED_CANARY_PERCENTAGES else None


def sign_release_authorization(
    payload: Mapping[str, Any],
    *,
    signing_key: str,
) -> dict[str, Any]:
    """Return a signed document; intended for the explicit Phase E operator tool."""

    if len(str(signing_key)) < 32:
        raise ValueError("release authorization signing key must contain at least 32 characters")
    unsigned = dict(payload)
    unsigned.pop("signature", None)
    signature = hmac.new(
        str(signing_key).encode("utf-8"),
        _canonical_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return {**unsigned, "signature": signature}


def validate_release_authorization(
    document: Mapping[str, Any],
    *,
    signing_key: str,
    expected_source_digest: str,
    configured_percentage: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if document.get("contract_version") != RELEASE_AUTHORIZATION_VERSION:
        reasons.append("release_authorization_contract_invalid")
    if len(str(signing_key)) < 32:
        reasons.append("release_authorization_key_unavailable")
    unsigned = dict(document)
    supplied_signature = str(unsigned.pop("signature", ""))
    expected_signature = (
        hmac.new(
            str(signing_key).encode("utf-8"),
            _canonical_bytes(unsigned),
            hashlib.sha256,
        ).hexdigest()
        if len(str(signing_key)) >= 32
        else ""
    )
    if not supplied_signature or not hmac.compare_digest(supplied_signature, expected_signature):
        reasons.append("release_authorization_signature_invalid")

    source_digest = str(document.get("release_source_digest") or "")
    if not _sha256_digest(source_digest) or source_digest != str(expected_source_digest):
        reasons.append("release_source_digest_mismatch")
    if not _sha256_digest(document.get("freeze_digest")):
        reasons.append("release_freeze_digest_invalid")
    authorization_id = str(document.get("authorization_id") or "").strip()
    if not authorization_id:
        reasons.append("release_authorization_id_missing")

    authorized_at = _timestamp(document.get("authorized_at"))
    expires_at = _timestamp(document.get("expires_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if authorized_at is None or expires_at is None or not authorized_at <= current < expires_at:
        reasons.append("release_authorization_time_window_invalid")

    authorized_percentage = document.get("authorized_canary_percentage")
    if (
        isinstance(authorized_percentage, bool)
        or not isinstance(authorized_percentage, int)
        or authorized_percentage not in ALLOWED_CANARY_PERCENTAGES
        or configured_percentage not in ALLOWED_CANARY_PERCENTAGES
        or (
            isinstance(authorized_percentage, int)
            and configured_percentage > authorized_percentage
        )
    ):
        reasons.append("release_canary_percentage_not_authorized")

    phases = document.get("completed_release_phases")
    if not isinstance(phases, Mapping) or any(phases.get(phase) is not True for phase in "ABCD"):
        reasons.append("release_phases_a_through_d_incomplete")
    gates = document.get("release_gates")
    if not isinstance(gates, Mapping):
        reasons.append("release_gate_evidence_missing")
        gates = {}
    if gates.get("full_test_suite") is not True:
        reasons.append("release_full_test_suite_not_passed")
    if gates.get("human_quality_gate") is not True:
        reasons.append("release_human_quality_gate_not_passed")
    if gates.get("security_review") is not True:
        reasons.append("release_security_review_not_passed")
    if gates.get("phase_d_environment") is not True:
        reasons.append("release_phase_d_environment_not_passed")
    statistical_gate = gates.get("statistical_release_gate")
    if not isinstance(statistical_gate, Mapping):
        statistical_gate = {}
    if (
        statistical_gate.get("contract_version")
        != PROSPECTIVE_EVIDENCE_CONTRACT_VERSION
    ):
        reasons.append("release_statistical_gate_contract_invalid")
    if statistical_gate.get("result") != "predictive_canary":
        reasons.append("release_statistical_gate_not_passed")
    denominator_shrinkage = statistical_gate.get("denominator_shrinkage")
    if isinstance(denominator_shrinkage, bool) or denominator_shrinkage != 0:
        reasons.append("release_statistical_denominator_shrinkage_not_zero")
    if statistical_gate.get("candidate_safety_hard_gates_zero") is not True:
        reasons.append("release_statistical_safety_hard_gates_not_zero")

    legal_gate = gates.get("external_legal_gate")
    if not isinstance(legal_gate, Mapping):
        legal_gate = {}
    if legal_gate.get("packet_version") != LEGAL_REVIEW_PACKET_VERSION:
        reasons.append("release_external_legal_packet_invalid")
    if legal_gate.get("state") != LEGAL_APPROVAL_STATE:
        reasons.append("release_external_legal_gate_not_approved")
    if not _sha256_digest(legal_gate.get("written_decision_digest")):
        reasons.append("release_external_legal_decision_digest_invalid")
    if legal_gate.get("public_directional_candidate_allowed") is not True:
        reasons.append("release_external_legal_scope_not_authorized")
    for field in _ZERO_VIOLATION_FIELDS:
        value = gates.get(field)
        if isinstance(value, bool) or value != 0:
            reasons.append(f"release_{field}_not_zero")

    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "contract_version": RELEASE_AUTHORIZATION_VERSION,
        "authorized": not unique_reasons,
        "authorization_id": authorization_id or None,
        "configured_canary_percentage": configured_percentage,
        "authorized_canary_percentage": (
            authorized_percentage if isinstance(authorized_percentage, int) else None
        ),
        "release_source_digest": source_digest or None,
        "expires_at": document.get("expires_at"),
        "reason_codes": unique_reasons or ["authorized"],
    }


def _authorization_path() -> Path | None:
    configured = env_text("LINE_MODEL_V2_RELEASE_AUTHORIZATION_PATH").strip()
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def release_authorization_status(
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    rollout = configured_release_rollout()
    percentage = configured_canary_percentage()
    if rollout != "canary":
        return {
            "contract_version": RELEASE_AUTHORIZATION_VERSION,
            "rollout": rollout,
            "authorized": False,
            "configured_canary_percentage": percentage,
            "reason_codes": ["candidate_delivery_kill_switch_active"],
        }
    if percentage is None:
        return {
            "contract_version": RELEASE_AUTHORIZATION_VERSION,
            "rollout": rollout,
            "authorized": False,
            "configured_canary_percentage": None,
            "reason_codes": ["release_canary_percentage_invalid"],
        }
    path = _authorization_path()
    if path is None or not path.is_file():
        return {
            "contract_version": RELEASE_AUTHORIZATION_VERSION,
            "rollout": rollout,
            "authorized": False,
            "configured_canary_percentage": percentage,
            "reason_codes": ["release_authorization_file_unavailable"],
        }
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        document = None
    if not isinstance(document, Mapping):
        return {
            "contract_version": RELEASE_AUTHORIZATION_VERSION,
            "rollout": rollout,
            "authorized": False,
            "configured_canary_percentage": percentage,
            "reason_codes": ["release_authorization_file_invalid"],
        }
    status = validate_release_authorization(
        document,
        signing_key=env_text("LINE_MODEL_V2_RELEASE_AUTHORIZATION_KEY"),
        expected_source_digest=str(
            RELEASE_RUNTIME_SOURCE_FINGERPRINT.get("source_digest") or ""
        ),
        configured_percentage=percentage,
        now=now,
    )
    return {"rollout": rollout, **status}


def candidate_delivery_decision(
    cohort_key: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    status = release_authorization_status(now=now)
    normalized_key = str(cohort_key or "").strip()
    if not status.get("authorized"):
        return {**status, "selected": False, "cohort_bucket": None}
    if not normalized_key:
        return {
            **status,
            "authorized": False,
            "selected": False,
            "cohort_bucket": None,
            "reason_codes": ["release_canary_cohort_key_missing"],
        }
    authorization_id = str(status.get("authorization_id") or "")
    key = env_text("LINE_MODEL_V2_RELEASE_AUTHORIZATION_KEY")
    digest = hmac.new(
        key.encode("utf-8"),
        f"{authorization_id}|{normalized_key}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10_000
    percentage = int(status["configured_canary_percentage"])
    selected = bucket < percentage * 100
    return {
        **status,
        "selected": selected,
        "cohort_bucket": bucket,
        "reason_codes": ["selected_for_canary" if selected else "outside_canary_cohort"],
    }
