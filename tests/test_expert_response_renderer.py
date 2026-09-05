from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.expert_response_renderer import render_expert_response  # noqa: E402


def _snapshot(*, partial: bool = False) -> dict:
    referee = {
        "decision_ready": not partial,
        "partial_analysis_available": partial,
        "main_status": "可觀察" if not partial else "資料部分可用",
        "main_reasons": ["趨勢仍有延續條件", "量能尚未充分確認"],
        "component_coverage": {"support": True, "resistance": not partial},
    }
    return {
        "code": "2646",
        "trade_date": "2026-09-01",
        "stock": {"name": "星宇航空"},
        "analysis_status": {"status": "ready" if not partial else "insufficient_data"},
        "referee": referee,
        "technical": {
            "decision_ready": True,
            "macd": {"dif": 0.4, "signal": 0.2, "oscillator": 0.2},
        },
    }


def test_natural_response_leads_with_event_and_keeps_one_short_disclaimer() -> None:
    result = render_expert_response(
        entity_snapshots=[({"code": "2646", "name": "星宇航空"}, _snapshot())],
        scan={
            "scan_state": "verified_material",
            "verified_material_event_ids": ["event-1"],
        },
        evidence_ids_by_entity={"2646": ["fact-close"]},
        profile="comprehensive",
        conversation_projection={
            "position_state": "持有",
            "referenced_stocks": [{"code": "2454", "name": "聯發科"}],
        },
    )
    assert result["explanation_blocks"][0]["block_type"] == "material_event"
    assert "接著看星宇航空（2646）" in result["text"]
    assert "MACD 位於零軸上" in result["text"]
    assert "若已持有" in result["text"]
    assert result["disclaimer_count"] == 1
    assert "真人" not in result["text"] and "持牌" not in result["text"]
    assert all("source_fields" in block for block in result["explanation_blocks"])


def test_partial_component_answers_reliable_parts_without_fake_zone_or_verdict() -> None:
    result = render_expert_response(
        entity_snapshots=[({"code": "2646", "name": "星宇航空"}, _snapshot(partial=True))],
        scan={"scan_state": "scan_incomplete", "incomplete_scopes": ["taiwan_policy"]},
        evidence_ids_by_entity={"2646": ["fact-close"]},
        profile="focused",
        conversation_projection={"position_state": "空手"},
    )
    assert "資料部分可用，但目前不形成主結論" in result["text"]
    assert "賣壓區尚未可靠形成" in result["text"]
    assert "事件安全掃描仍有未完成範圍" in result["text"]
    assert "若目前空手" in result["text"]
    assert result["disclaimer_count"] == 1
