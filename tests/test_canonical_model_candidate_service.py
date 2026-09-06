from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from adapter.qwen_local import QwenChatResult  # noqa: E402
from services import canonical_model_candidate_service as candidate_service  # noqa: E402
from services.canonical_model_packet_service import (  # noqa: E402
    build_canonical_model_fact_packet_v2,
)


def _artifact() -> dict:
    return {
        "analysis_id": "analysis-candidate-1",
        "snapshot_id": "snapshot-candidate-1",
        "analysis_cutoff": "2026-09-01T13:45:00+08:00",
        "validity": "partial",
        "omissions": [
            {"scope": "events", "reason": "no_event_before_cutoff"},
            {"scope": "risk", "reason": "risk_fact_unavailable"},
        ],
        "analysis": {
            "analysis_id": "analysis-candidate-1",
            "snapshot_id": "snapshot-candidate-1",
            "analysis_cutoff": "2026-09-01T13:45:00+08:00",
            "trade_date": "2026-09-01",
            "profile": "focused",
            "target_entities": [{"code": "2330", "name": "台積電"}],
            "entity_analyses": [
                {
                    "entity": {"code": "2330", "name": "台積電"},
                    "trade_date": "2026-09-01",
                    "ohlcv": {"close": 1200.0, "volume": 10_000_000},
                    "technical_ensemble": {
                        "status": "ok",
                        "overall_score": 0.35,
                        "family_scores": {"trend_score": 0.4},
                    },
                }
            ],
            "referee": {"decision_ready": True, "main_status": "觀察"},
        },
    }


def _packet() -> dict:
    return build_canonical_model_fact_packet_v2(
        _artifact(),
        requested_scopes=["price", "technical", "events", "risk"],
    )


def _valid_output(packet: dict) -> str:
    scope_ids = packet["render_contract"]["scope_evidence_ids"]
    limitation_by_id = {
        item["evidence_ids"][0]: item["text_template"]
        for item in packet["render_contract"]["output_rules"]["limitation_blocks_exact"]
    }
    blocks = [
        {
            "block_type": "fact",
            "text_template": "官方價格資料可供判讀。",
            "evidence_ids": [scope_ids["price"][0]],
            "uncertainty": "low",
            "conditions": [],
        },
        {
            "block_type": "inference",
            "text_template": "技術資料僅供條件式判讀。",
            "evidence_ids": [scope_ids["technical"][0]],
            "uncertainty": "medium",
            "conditions": [],
        },
    ]
    for scope in ("events", "risk"):
        evidence_id = scope_ids[scope][0]
        blocks.append(
            {
                "block_type": "limitation",
                "text_template": limitation_by_id[evidence_id],
                "evidence_ids": [evidence_id],
                "uncertainty": "high",
                "conditions": [],
            }
        )
    return json.dumps(
        {
            "contract_version": "model-analysis-v2",
            "explanation_blocks": blocks,
            "missing_data": packet["render_contract"]["output_rules"]["missing_data_exact"],
            "used_event_ids": [],
            "research_limitations": [],
        },
        ensure_ascii=False,
    )


def _run_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_callable, *, on_start=None, cancellable_callable=None):
        assert cancellable_callable is not None
        if on_start:
            on_start(7)
        return SimpleNamespace(value=cancellable_callable(__import__("threading").Event()))

    monkeypatch.setattr(candidate_service, "run_shadow_model", fake_run)


def test_candidate_is_shadow_only_and_uses_deterministic_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _packet()
    captured: dict = {}
    _run_inline(monkeypatch)
    monkeypatch.setenv("QWEN_CONTEXT_TOKENS", "16384")

    def fake_qwen(*_args, **kwargs):
        captured.update(kwargs)
        return QwenChatResult(
            text=_valid_output(packet),
            finish_reason="stop",
            prompt_tokens=800,
            completion_tokens=90,
            model="taiwan-stock-qwen:latest",
        )

    monkeypatch.setattr(candidate_service, "qwen_chat_detailed", fake_qwen)

    result = candidate_service.run_canonical_model_candidate(
        "請分析價格、技術、事件與風險",
        packet,
        execution_mode="offline",
    )

    assert captured["temperature"] == 0.0
    assert captured["seed"] == 20260901
    assert captured["response_schema"]["additionalProperties"] is False
    assert result["candidate_can_replace_stable"] is False
    assert result["candidate_can_override_referee"] is False
    assert result["queue_wait_ms"] == 7
    assert result["validator_result"] == "pass"
    assert result["raw_validator_result"] == "pass"
    assert result["depth"] == "focused"
    assert result["scopes"] == ["price", "technical", "events", "risk"]
    assert set(result["stage_timings_ms"]) == {
        "packet_build", "generation", "validation",
    }
    assert result["stage_timings_ms"]["generation"] >= 0


def test_candidate_validator_rejects_referee_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _packet()
    _run_inline(monkeypatch)
    output = json.loads(_valid_output(packet))
    output["main_status"] = "買進"
    monkeypatch.setattr(
        candidate_service,
        "qwen_chat_detailed",
        lambda *_args, **_kwargs: QwenChatResult(
            text=json.dumps(output, ensure_ascii=False),
            finish_reason="stop",
            prompt_tokens=800,
            completion_tokens=90,
            model="taiwan-stock-qwen:latest",
        ),
    )

    result = candidate_service.run_canonical_model_candidate("分析", packet)

    assert result["validator_result"] == "reject"
    assert "referee_override_attempt" in result["validator_reason_codes"]
    assert result["referee_override_count"] == 1


def test_candidate_rejects_user_visible_execution_mode_without_model_call() -> None:
    with pytest.raises(ValueError, match="offline or shadow"):
        candidate_service.run_canonical_model_candidate(
            "分析",
            _packet(),
            execution_mode="stable",
        )


def test_interactive_candidate_uses_work_stop_deadline_and_no_background_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _packet()
    captured: dict = {}

    def fake_qwen(*_args, **kwargs):
        captured.update(kwargs)
        return QwenChatResult(
            text=_valid_output(packet),
            finish_reason="stop",
            prompt_tokens=800,
            completion_tokens=90,
            model="taiwan-stock-qwen:latest",
        )

    def fake_interactive(callable_, **kwargs):
        captured["admission"] = kwargs
        return SimpleNamespace(value=callable_(), queue_wait_ms=9)

    monkeypatch.setattr(candidate_service, "qwen_chat_detailed", fake_qwen)
    monkeypatch.setattr(candidate_service, "run_interactive_model", fake_interactive)
    monkeypatch.setattr(
        candidate_service, "time",
        SimpleNamespace(monotonic=lambda: 1000.0, perf_counter_ns=time.perf_counter_ns),
    )
    deadline = 1030.0

    result = candidate_service.run_canonical_model_candidate_interactive(
        "分析",
        packet,
        work_stop_deadline_monotonic=deadline,
    )

    assert captured["allow_background_timeout"] is False
    assert 5 <= captured["timeout_seconds"] <= 30
    assert captured["admission"]["deadline_monotonic"] == deadline
    assert result["queue_wait_ms"] == 9
    assert result["release_authorization_required_for_delivery"] is True
