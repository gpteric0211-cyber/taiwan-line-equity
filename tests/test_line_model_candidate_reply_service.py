from __future__ import annotations

import sys
from pathlib import Path

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.line_model_candidate_reply_service import (  # noqa: E402
    CandidateReplyRenderError,
    render_validated_candidate_reply_preview,
)


def _validated_result() -> dict[str, object]:
    return {
        "validator_result": "pass",
        "model_output": "RAW_MODEL_TEXT_MUST_NOT_RENDER",
        "compacted_packet": {
            "request": {"analysis_cutoff": "2026-08-28"},
            "referee": {
                "immutable": True,
                "main_status": "可觀察",
                "reasons": ["均線結構偏多，走勢維持穩定"],
            },
        },
        "explanation_blocks": [
            {"block_type": "fact"},
            {"block_type": "scenario"},
        ],
        "rendered_blocks": [
            "資料庫收盤與均線資料已通過品質檢查。",
            "若量能轉弱，仍需留意趨勢延續性。",
        ],
    }


def test_candidate_preview_uses_immutable_referee_and_validator_rendered_blocks_only() -> None:
    preview = render_validated_candidate_reply_preview(
        _validated_result(),
        stock_code="2330",
        stock_name="台積電",
    )

    assert "主結論：可觀察" in preview["text"]
    assert "裁判依據：均線結構偏多，走勢維持穩定" in preview["text"]
    assert "資料庫收盤與均線資料已通過品質檢查" in preview["text"]
    assert "RAW_MODEL_TEXT_MUST_NOT_RENDER" not in preview["text"]
    assert preview["text"].endswith("僅供資料整理，不構成投資建議。")
    assert preview["raw_model_output_used"] is False
    assert preview["candidate_can_replace_reply"] is False


def test_candidate_preview_rejects_unvalidated_output() -> None:
    result = _validated_result()
    result["validator_result"] = "reject"

    with pytest.raises(CandidateReplyRenderError) as captured:
        render_validated_candidate_reply_preview(
            result,
            stock_code="2330",
            stock_name="台積電",
        )

    assert captured.value.reason_code == "candidate_validator_not_passed"


def test_candidate_preview_rejects_mutable_or_missing_referee() -> None:
    result = _validated_result()
    result["compacted_packet"]["referee"]["immutable"] = False  # type: ignore[index]

    with pytest.raises(CandidateReplyRenderError) as captured:
        render_validated_candidate_reply_preview(
            result,
            stock_code="2330",
            stock_name="台積電",
        )

    assert captured.value.reason_code == "immutable_referee_missing"
