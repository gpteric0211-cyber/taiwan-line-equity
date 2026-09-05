from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core import line_model_release_config as release_config  # noqa: E402


NOW = datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc)
SOURCE_DIGEST = "a" * 64
SIGNING_KEY = "release-test-key-that-is-longer-than-thirty-two-characters"


def _payload(*, percentage: int = 25) -> dict:
    return {
        "contract_version": release_config.RELEASE_AUTHORIZATION_VERSION,
        "authorization_id": "release-auth-20260901",
        "authorized_at": (NOW - timedelta(minutes=5)).isoformat(),
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "freeze_digest": "b" * 64,
        "release_source_digest": SOURCE_DIGEST,
        "authorized_canary_percentage": percentage,
        "completed_release_phases": {phase: True for phase in "ABCD"},
        "release_gates": {
            "full_test_suite": True,
            "human_quality_gate": True,
            "security_review": True,
            "phase_d_environment": True,
            "statistical_release_gate": {
                "contract_version": release_config.PROSPECTIVE_EVIDENCE_CONTRACT_VERSION,
                "result": "predictive_canary",
                "denominator_shrinkage": 0,
                "candidate_safety_hard_gates_zero": True,
            },
            "external_legal_gate": {
                "packet_version": release_config.LEGAL_REVIEW_PACKET_VERSION,
                "state": release_config.LEGAL_APPROVAL_STATE,
                "written_decision_digest": "d" * 64,
                "public_directional_candidate_allowed": True,
            },
            "protected_analysis_diff": 0,
            "ungrounded_numeric_or_date_claims": 0,
            "referee_overrides": 0,
            "expired_or_reused_reply_tokens": 0,
            "wrong_entity_resolution": 0,
            "unique_alias_unnecessary_confirmation": 0,
            "web_line_canonical_mismatch": 0,
            "raw_image_or_full_ocr_persistence": 0,
        },
    }


def test_release_authorization_validates_every_phase_and_hard_gate() -> None:
    document = release_config.sign_release_authorization(
        _payload(), signing_key=SIGNING_KEY
    )

    status = release_config.validate_release_authorization(
        document,
        signing_key=SIGNING_KEY,
        expected_source_digest=SOURCE_DIGEST,
        configured_percentage=10,
        now=NOW,
    )

    assert status["authorized"] is True
    assert status["reason_codes"] == ["authorized"]


def test_release_authorization_rejects_tampering_expiry_and_source_mismatch() -> None:
    document = release_config.sign_release_authorization(
        _payload(), signing_key=SIGNING_KEY
    )
    document["release_gates"]["referee_overrides"] = 1

    status = release_config.validate_release_authorization(
        document,
        signing_key=SIGNING_KEY,
        expected_source_digest="c" * 64,
        configured_percentage=50,
        now=NOW + timedelta(hours=2),
    )

    assert status["authorized"] is False
    assert "release_authorization_signature_invalid" in status["reason_codes"]
    assert "release_source_digest_mismatch" in status["reason_codes"]
    assert "release_authorization_time_window_invalid" in status["reason_codes"]
    assert "release_canary_percentage_not_authorized" in status["reason_codes"]
    assert "release_referee_overrides_not_zero" in status["reason_codes"]


def test_legacy_statistical_string_and_missing_legal_decision_fail_closed() -> None:
    payload = _payload()
    payload["release_gates"]["statistical_release_gate"] = "pass_for_canary"
    payload["release_gates"].pop("external_legal_gate")
    document = release_config.sign_release_authorization(
        payload, signing_key=SIGNING_KEY
    )

    status = release_config.validate_release_authorization(
        document,
        signing_key=SIGNING_KEY,
        expected_source_digest=SOURCE_DIGEST,
        configured_percentage=10,
        now=NOW,
    )

    assert status["authorized"] is False
    assert "release_statistical_gate_contract_invalid" in status["reason_codes"]
    assert "release_statistical_gate_not_passed" in status["reason_codes"]
    assert "release_external_legal_gate_not_approved" in status["reason_codes"]
    assert "release_external_legal_decision_digest_invalid" in status["reason_codes"]


def test_kill_switch_prevents_file_or_candidate_selection(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    monkeypatch.delenv("LINE_MODEL_V2_RELEASE_AUTHORIZATION_PATH", raising=False)

    status = release_config.release_authorization_status(now=NOW)
    decision = release_config.candidate_delivery_decision("event-123", now=NOW)

    assert status["authorized"] is False
    assert status["reason_codes"] == ["candidate_delivery_kill_switch_active"]
    assert decision["selected"] is False


def test_signed_canary_selection_is_deterministic_and_never_exposes_secret(
    tmp_path: Path, monkeypatch
) -> None:
    document = release_config.sign_release_authorization(
        _payload(percentage=100), signing_key=SIGNING_KEY
    )
    path = tmp_path / "release-authorization.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "canary")
    monkeypatch.setenv("LINE_MODEL_V2_CANARY_PERCENT", "100")
    monkeypatch.setenv("LINE_MODEL_V2_RELEASE_AUTHORIZATION_PATH", str(path))
    monkeypatch.setenv("LINE_MODEL_V2_RELEASE_AUTHORIZATION_KEY", SIGNING_KEY)
    monkeypatch.setitem(
        release_config.RELEASE_RUNTIME_SOURCE_FINGERPRINT,
        "source_digest",
        SOURCE_DIGEST,
    )

    first = release_config.candidate_delivery_decision("event-123", now=NOW)
    second = release_config.candidate_delivery_decision("event-123", now=NOW)

    assert first == second
    assert first["authorized"] is True
    assert first["selected"] is True
    assert 0 <= first["cohort_bucket"] < 10_000
    assert SIGNING_KEY not in json.dumps(first)


def test_invalid_rollout_or_percentage_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "production")
    assert release_config.configured_release_rollout() == "off"

    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "canary")
    monkeypatch.setenv("LINE_MODEL_V2_CANARY_PERCENT", "17")
    status = release_config.release_authorization_status(now=NOW)
    assert status["authorized"] is False
    assert status["reason_codes"] == ["release_canary_percentage_invalid"]
