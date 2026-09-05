from __future__ import annotations

import json
import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services import line_canonical_model_service as canonical_service  # noqa: E402


def test_prepare_is_off_by_default_and_captures_point_in_time_without_io(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "off")
    assert canonical_service.prepare_line_canonical_candidate(
        question="台積電技術面",
        model_facts={"code": "2330"},
        focus="overview",
    ) is None

    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    request = canonical_service.prepare_line_canonical_candidate(
        question="台積電技術面與風險",
        model_facts={"code": "2330"},
        focus="overview",
        conversation_context={"active_stock": {"code": "2330"}},
    )
    assert request is not None
    assert request["profile"] == "focused"
    assert request["requested_scopes"] == ["technical", "risk"]
    assert request["analysis_cutoff"] == request["request_received_at"]
    assert len(request["request_id"]) == 32
    assert request["raw_request_persisted"] is False

    readiness = canonical_service.line_canonical_model_readiness()
    assert readiness["shadow_enabled"] is True
    assert readiness["candidate_can_replace_reply"] is False
    assert readiness["legacy_projection_used_for_candidate"] is False


def test_post_reply_shadow_uses_canonical_packet_and_persists_only_sanitized_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LINE_MODEL_V2_ROLLOUT", "shadow")
    monkeypatch.setattr(canonical_service, "qwen_model_resident", lambda: True)
    request = canonical_service.prepare_line_canonical_candidate(
        question="台積電技術面",
        model_facts={"code": "2330"},
        focus="overview",
    )
    observed: dict[str, object] = {}

    def fetcher(query: str, **kwargs) -> dict:
        observed["query"] = query
        observed["fetch"] = kwargs
        return {
            "packet_ready": True,
            "analysis_id": "analysis-1",
            "snapshot_id": "snapshot-1",
            "analysis_cutoff": request["analysis_cutoff"],
            "canonical_answer_text_hash": "a" * 64,
            "packet_digest": "b" * 64,
            "packet": {"contract_version": "model-fact-packet-v2"},
        }

    def runner(question: str, packet: dict, **kwargs) -> dict:
        observed["runner"] = (question, packet, kwargs)
        return {
            "validator_result": "pass",
            "validator_reason_codes": [],
            "model_output_sha256": "c" * 64,
            "profile": "focused-16k-v1",
            "packet_token_count": 1200,
            "estimated_prompt_token_count": 1300,
            "finish_reason": "stop",
            "ungrounded_claim_count": 0,
            "referee_override_count": 0,
            "queue_wait_ms": 2,
            "model_latency_ms": 3,
            "total_duration_ms": 5,
        }

    evidence_path = tmp_path / "canonical-shadow.jsonl"
    future = canonical_service.submit_line_canonical_shadow(
        request,
        stable_reply_sha256="d" * 64,
        evidence_path=evidence_path,
        packet_fetcher=fetcher,
        candidate_runner=runner,
    )
    assert future is not None
    result = future.result(timeout=2)

    assert observed["runner"][2]["execution_mode"] == "shadow"
    assert result["status"] == "completed"
    assert result["request_id"] == request["request_id"]
    assert result["packet_token_count"] == 1200
    assert result["finish_reason"] == "stop"
    assert result["candidate_reply_sent"] is False
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted["analysis_id"] == "analysis-1"
    assert "question" not in persisted
    assert "packet" not in persisted
    assert persisted["raw_request_or_packet_persisted"] is False


def test_canary_readiness_remains_parity_guarded_even_when_authorized(monkeypatch) -> None:
    monkeypatch.setattr(canonical_service, "configured_release_rollout", lambda: "canary")
    monkeypatch.setattr(
        canonical_service,
        "release_authorization_status",
        lambda: {"authorized": True, "authorization_id": "auth-1"},
    )

    readiness = canonical_service.line_canonical_model_readiness()

    assert readiness["candidate_can_replace_reply"] is False
    assert readiness["candidate_delivery_integration"] == (
        "complete_fail_closed_parity_guarded"
    )
    assert readiness["candidate_replacement_gate"] == (
        "authorized_backend_revalidation_and_immutable_model_answer_persistence"
    )
    assert readiness["canonical_model_answer_persistence"] == "implemented_fail_closed"
