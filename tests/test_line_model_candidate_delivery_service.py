from __future__ import annotations

import sys
from pathlib import Path


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.line_model_candidate_delivery_service import (  # noqa: E402
    select_canonical_candidate_reply,
)
from services.line_model_candidate_reply_service import (  # noqa: E402
    render_validated_candidate_reply_preview,
)


def _candidate() -> dict:
    candidate = {
        "validator_result": "pass",
        "candidate_can_override_referee": False,
        "compacted_packet": {
            "artifact_identity": {
                "analysis_id": "analysis-2330",
                "canonical_answer_text_hash": "a" * 64,
            },
            "request": {"analysis_cutoff": "2026-09-01T13:30:00+08:00"},
            "referee": {
                "immutable": True,
                "main_status": "資料充足但等待條件確認",
                "reasons": ["裁判結論不可由模型覆寫"],
            },
            "events": [],
        },
        "explanation_blocks": [
            {
                "block_type": "fact",
                "text_template": "資料基準完整。",
                "evidence_ids": ["F001"],
                "uncertainty": "low",
                "conditions": [],
            }
        ],
        "rendered_blocks": ["資料基準完整。"],
        "used_event_ids": [],
    }
    preview = render_validated_candidate_reply_preview(
        candidate,
        stock_code="2330",
        stock_name="台積電",
    )
    candidate["compacted_packet"]["artifact_identity"][
        "canonical_answer_text_hash"
    ] = preview["sha256"]
    return candidate


def _decision(*, selected: bool = True) -> dict:
    return {
        "authorized": True,
        "selected": selected,
        "reason_codes": ["selected_for_canary" if selected else "outside_canary_cohort"],
        "configured_canary_percentage": 5,
        "cohort_bucket": 123,
        "authorization_id": "auth-1",
    }


def _finalized(candidate: dict) -> dict:
    preview = render_validated_candidate_reply_preview(
        candidate,
        stock_code="2330",
        stock_name="台積電",
    )
    return {
        "analysis_id": candidate["compacted_packet"]["artifact_identity"]["analysis_id"],
        "canonical_answer_text": preview["text"],
        "canonical_answer_text_hash": preview["sha256"],
        "raw_model_output_persisted": False,
    }


def test_candidate_delivery_requires_release_selection_and_validator_pass() -> None:
    accepted_candidate = _candidate()
    delivered = select_canonical_candidate_reply(
        stable_reply="穩定回答",
        candidate_result=accepted_candidate,
        stock_code="2330",
        stock_name="台積電",
        cohort_key="event-1",
        completed_before_work_stop=True,
        canonical_model_answer=_finalized(accepted_candidate),
        decision_provider=lambda _key: _decision(),
    )
    rejected_candidate = _candidate()
    rejected_candidate["validator_result"] = "reject"
    fallback = select_canonical_candidate_reply(
        stable_reply="穩定回答",
        candidate_result=rejected_candidate,
        stock_code="2330",
        stock_name="台積電",
        cohort_key="event-1",
        completed_before_work_stop=True,
        decision_provider=lambda _key: _decision(),
    )

    assert delivered["delivery_path"] == "canonical_candidate"
    assert delivered["candidate_delivered"] is True
    assert "主結論" in delivered["text"]
    assert fallback["delivery_path"] == "stable_fallback"
    assert fallback["text"] == "穩定回答"
    assert fallback["candidate_reason"] == "candidate_validator_not_passed"


def test_kill_switch_cohort_and_work_stop_always_preserve_stable_reply() -> None:
    for decision, work_stop, reason in (
        (_decision(selected=False), True, "outside_canary_cohort"),
        (_decision(selected=True), False, "candidate_missed_work_stop_budget"),
        ({"authorized": False, "selected": False, "reason_codes": ["kill_switch"]}, True, "kill_switch"),
    ):
        result = select_canonical_candidate_reply(
            stable_reply="穩定回答",
            candidate_result=_candidate(),
            stock_code="2330",
            stock_name="台積電",
            cohort_key="event-1",
            completed_before_work_stop=work_stop,
            decision_provider=lambda _key, value=decision: value,
        )
        assert result["delivery_path"] == "stable_fallback"
        assert result["text"] == "穩定回答"
        assert result["candidate_reason"] == reason


def test_candidate_without_canonical_artifact_binding_fails_closed() -> None:
    candidate = _candidate()
    candidate["compacted_packet"]["artifact_identity"] = {}

    result = select_canonical_candidate_reply(
        stable_reply="穩定回答",
        candidate_result=candidate,
        stock_code="2330",
        stock_name="台積電",
        cohort_key="event-1",
        completed_before_work_stop=True,
        decision_provider=lambda _key: _decision(),
    )

    assert result["candidate_delivered"] is False
    assert result["candidate_reason"] == "candidate_canonical_artifact_binding_missing"
    assert result["raw_candidate_persisted"] is False


def test_candidate_text_must_match_shared_web_line_canonical_hash() -> None:
    candidate = _candidate()
    finalized = _finalized(candidate)
    finalized["canonical_answer_text_hash"] = "f" * 64

    result = select_canonical_candidate_reply(
        stable_reply="穩定回答",
        candidate_result=candidate,
        stock_code="2330",
        stock_name="台積電",
        cohort_key="event-1",
        completed_before_work_stop=True,
        canonical_model_answer=finalized,
        decision_provider=lambda _key: _decision(),
    )

    assert result["candidate_delivered"] is False
    assert result["candidate_reason"] == "candidate_web_line_canonical_hash_mismatch"
    assert result["text"] == "穩定回答"
