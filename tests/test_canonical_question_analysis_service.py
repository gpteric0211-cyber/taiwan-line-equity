from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from services.canonical_question_analysis_service import run_canonical_question_analysis  # noqa: E402


def test_unique_new_entity_overrides_old_active_context_without_confirmation() -> None:
    resolver = Mock(
        return_value={
            "ok": True,
            "status": "ok",
            "stock": {"code": "2646", "name": "星宇航空"},
            "entities": [{"code": "2646", "name": "星宇航空"}],
            "comparison_stocks": [],
            "requires_confirmation": False,
        }
    )
    orchestrator = Mock(return_value={"ok": True, "analysis_id": "analysis-2646"})
    result = run_canonical_question_analysis(
        query="所以星宇呢",
        delivery_channel="line",
        conversation_context={
            "code": "2454",
            "stock_name": "聯發科",
            "recent_exchanges": [{"user": "聯發科呢", "assistant": "先前回答"}],
        },
        resolver=resolver,
        orchestrator=orchestrator,
    )
    resolver.assert_called_once_with("所以星宇呢", active_stock_code="2454")
    call = orchestrator.call_args.kwargs
    assert call["code"] == "2646"
    assert call["delivery_channel"] == "line"
    assert call["conversation_projection"]["active_stock"]["code"] == "2646"
    assert result["requires_clarification"] is False
    assert set(result["stage_timings_ms"]) == {
        "classification", "projection", "retrieval",
    }


def test_conflict_returns_clarification_without_creating_artifact() -> None:
    resolver = Mock(
        return_value={
            "ok": False,
            "status": "conflict",
            "candidates": [{"code": "2317"}, {"code": "2330"}],
        }
    )
    orchestrator = Mock()
    result = run_canonical_question_analysis(
        query="2317 台積電",
        delivery_channel="web",
        resolver=resolver,
        orchestrator=orchestrator,
    )
    assert result["status"] == "clarification_required"
    assert result["artifact_created"] is False
    assert result["stage_timings_ms"]["classification"] >= 0
    orchestrator.assert_not_called()
