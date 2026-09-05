from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


REVIEW_SRC = Path(__file__).resolve().parents[1] / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from api import line_model_benchmark as benchmark_api  # noqa: E402
from services import line_model_benchmark_service as benchmark_service  # noqa: E402
from scripts import run_line_model_live_acceptance as acceptance_runner  # noqa: E402


def test_benchmark_api_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LINE_MODEL_BENCHMARK_ENABLED", raising=False)

    with pytest.raises(HTTPException) as captured:
        benchmark_api.shadow_sample(
            {"scenario": "fundamental_chip_technical_news", "code": "2330"}
        )

    assert captured.value.status_code == 404


def test_source_fingerprint_is_read_only_and_bound_to_imported_runtime(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")

    result = benchmark_api.source_fingerprint()

    assert result["complete"] is True
    assert len(result["source_digest"]) == 64
    assert result["source_hashes"] == benchmark_api.LOADED_RUNTIME_SOURCE_FINGERPRINT["source_hashes"]
    assert all(not Path(relative).is_absolute() for relative in result["source_hashes"])


def test_benchmark_api_accepts_only_fixed_scenarios(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")

    with pytest.raises(HTTPException) as captured:
        benchmark_api.shadow_sample({"scenario": "arbitrary-user-prompt", "code": "2330"})

    assert captured.value.status_code == 422


def test_interactive_warmup_requires_explicit_delay_acknowledgement(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")

    with pytest.raises(HTTPException) as captured:
        benchmark_api.interactive_warmup({})

    assert captured.value.status_code == 422


def test_background_warmup_requires_runtime_flag(monkeypatch) -> None:
    monkeypatch.delenv("LINE_MODEL_BACKGROUND_PREWARM", raising=False)

    with pytest.raises(HTTPException) as captured:
        benchmark_api.background_warmup({})

    assert captured.value.status_code == 404


def test_background_warmup_schedules_without_waiting(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_BACKGROUND_PREWARM", "true")
    monkeypatch.setattr(
        benchmark_api,
        "schedule_live_background_warmup",
        lambda: {"status": "scheduled", "scheduled": True},
    )

    assert benchmark_api.background_warmup({}) == {
        "status": "scheduled",
        "scheduled": True,
    }


@pytest.mark.parametrize(
    "endpoint",
    (
        benchmark_api.memory_contention_probe,
        benchmark_api.vision_contention_probe,
        benchmark_api.vision_stable_reply_probe,
        benchmark_api.cold_load_probe,
    ),
)
def test_contention_and_cold_benchmarks_require_delay_acknowledgement(
    monkeypatch,
    endpoint,
) -> None:
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")

    with pytest.raises(HTTPException) as captured:
        endpoint({})

    assert captured.value.status_code == 422


def test_live_shadow_benchmark_refuses_cold_model(monkeypatch) -> None:
    monkeypatch.setattr(benchmark_service, "qwen_model_resident", lambda: False)

    with pytest.raises(benchmark_service.LineModelBenchmarkError) as captured:
        benchmark_service.run_live_shadow_sample(
            scenario="fundamental_chip_technical_news",
            code="2330",
        )

    assert captured.value.reason_code == "model_not_resident"


def test_live_shadow_benchmark_uses_canonical_packet_and_returns_sanitized_measurements(
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(benchmark_service, "qwen_model_resident", lambda: True)
    monkeypatch.setattr(
        benchmark_service,
        "prepare_line_canonical_candidate",
        lambda **kwargs: {
            "question": kwargs["question"],
            "requested_scopes": ["technical"],
            "conversation_context": {},
            "analysis_cutoff": "2026-09-01T13:30:00+08:00",
            "request_received_at": "2026-09-01T13:30:00+08:00",
            "profile": "focused",
        },
    )

    def fetch_packet(*args, **kwargs):
        observed["fetch"] = (args, kwargs)
        return {
            "packet_ready": True,
            "analysis_id": "analysis-1",
            "snapshot_id": "snapshot-1",
            "canonical_answer_text_hash": "a" * 64,
            "artifact_reused": False,
            "event_record_count": 1,
            "stage_timings_ms": {
                "classification": 1.0,
                "retrieval": 2.0,
                "projection": 3.0,
                "packet_build": 4.0,
            },
            "packet": {
                "contract_version": "model-fact-packet-v2",
                "events": [{"source_class": "news_radar"}],
            },
        }

    def run_candidate(question, packet, **kwargs):
        observed["candidate"] = (question, packet, kwargs)
        return {
            "candidate_version": "canonical-model-candidate-v1",
            "execution_mode": "shadow",
            "profile": "focused-16k-v1",
            "packet_token_count": 100,
            "estimated_prompt_token_count": 120,
            "prompt_token_count": 130,
            "completion_token_count": 40,
            "queue_wait_ms": 7,
            "model_latency_ms": 10,
            "total_duration_ms": 13,
            "finish_reason": "stop",
            "validator_result": "pass",
            "validator_reason_codes": ["pass"],
            "ungrounded_claim_count": 0,
            "referee_override_count": 0,
            "error_classification": None,
            "model_output_sha256": "b" * 64,
            "stage_timings_ms": {
                "packet_build": 5.0,
                "generation": 10.0,
                "validation": 2.0,
            },
        }

    monkeypatch.setattr(benchmark_service, "fetch_canonical_question_model_packet", fetch_packet)
    monkeypatch.setattr(benchmark_service, "run_canonical_model_candidate", run_candidate)
    monkeypatch.setattr(
        benchmark_service,
        "render_validated_candidate_reply_preview",
        lambda *_args, **_kwargs: {"sha256": "c" * 64, "text": "preview"},
    )
    monkeypatch.setattr(benchmark_service, "model_admission_snapshot", lambda: {"queue_depth": 0})

    result = benchmark_service.run_live_shadow_sample(
        scenario="focused_technical",
        code="2330",
    )

    assert observed["candidate"][2]["execution_mode"] == "shadow"
    assert result["benchmark_contract"] == "line-model-live-canonical-shadow-sample-v2"
    assert result["queue_wait_ms"] == 7
    assert result["result"]["analysis_id"] == "analysis-1"
    assert result["result"]["canonical_artifact_cache_state"] == "miss"
    assert result["result"]["canonical_news_state"] == "hit"
    assert result["result"]["canonical_news_event_count"] == 1
    assert result["result"]["stage_timings_ms"]["packet_build"] == 9.0
    assert result["result"]["raw_candidate_or_packet_returned"] is False
    assert "model_output" not in result["result"]
    assert "packet" not in result["result"]


def test_stable_reply_benchmark_uses_fixed_production_answer_path_without_exposing_text(
    monkeypatch,
) -> None:
    answer = "資料庫回覆\n僅供資料整理，不構成投資建議。"
    monkeypatch.setattr(
        benchmark_service,
        "_answer_stock_question_result",
        lambda *_args, **_kwargs: type(
            "Answer",
            (),
            {
                "text": answer,
                "answer_path": "admission_fallback:predicted_deadline_admission_rejected",
                "shadow_request": {"request_id": "prepared-only"},
                "model_output_sha256": None,
                "model_output_characters": None,
                "policy_rejection_reason": None,
            },
        )(),
    )
    monkeypatch.setattr(
        benchmark_service,
        "model_admission_snapshot",
        lambda: {"queue_depth": 0, "predicted_deadline_rejected": 1},
    )

    result = benchmark_service.run_live_stable_reply_sample(
        scenario="fundamental_chip_technical_news",
        code="2330",
    )

    assert result["answer_path"] == "admission_fallback:predicted_deadline_admission_rejected"
    assert result["reply_has_required_disclaimer"] is True
    assert result["completed_within_internal_reply_budget"] is True
    assert result["candidate_reply_submitted"] is False
    assert result["shadow_request_prepared"] is True
    assert result["policy_rejection_reason"] is None
    assert "reply_text" not in result


def test_stable_reply_benchmark_rejects_nonfixed_stock_code() -> None:
    with pytest.raises(benchmark_service.LineModelBenchmarkError) as captured:
        benchmark_service.run_live_stable_reply_sample(
            scenario="focused_technical",
            code="2317",
        )

    assert captured.value.reason_code == "invalid_code"


def test_candidate_preview_benchmark_renders_only_validator_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_service,
        "_execute_live_canonical_shadow_sample",
        lambda **_kwargs: {
            "response": {
                "question_sha256": "q" * 64,
                "request_id": "preview-1",
                "queue_wait_ms": 10,
                "execution_ms": 200,
                "benchmark_end_to_end_ms": 220,
                "result": {
                    "validator_result": "pass",
                    "validator_reason_codes": ["pass"],
                },
            },
            "candidate": {},
            "preview": {
                "text": "候選預覽",
                "raw_model_output_used": False,
                "candidate_can_replace_reply": False,
            },
        },
    )

    result = benchmark_service.run_live_candidate_reply_preview(
        scenario="fundamental_chip_technical_news",
        code="2330",
    )

    assert result["preview_rendered"] is True
    assert result["preview"]["text"] == "候選預覽"
    assert result["candidate_reply_sent_to_line"] is False
    assert result["candidate_can_replace_reply"] is False


def test_candidate_preview_benchmark_keeps_reject_reasons_without_rendering(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_service,
        "_execute_live_canonical_shadow_sample",
        lambda **_kwargs: {
            "response": {
                "question_sha256": "q" * 64,
                "request_id": "preview-reject",
                "queue_wait_ms": 10,
                "execution_ms": 200,
                "benchmark_end_to_end_ms": 220,
                "result": {
                    "validator_result": "reject",
                    "validator_reason_codes": ["ungrounded_numeric_or_date_claim"],
                },
            },
            "candidate": {},
            "preview": None,
        },
    )

    result = benchmark_service.run_live_candidate_reply_preview(
        scenario="fundamental_chip_technical_news",
        code="2330",
    )

    assert result["preview_rendered"] is False
    assert result["validator_reason_codes"] == ["ungrounded_numeric_or_date_claim"]
    assert "preview" not in result


def test_candidate_preview_api_uses_only_fixed_scenario(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")
    monkeypatch.setattr(
        benchmark_api,
        "run_live_candidate_reply_preview",
        lambda **kwargs: {"scenario": kwargs["scenario"], "preview_rendered": True},
    )

    result = benchmark_api.candidate_reply_preview(
        {"scenario": "technical_valuation_support_risk", "code": "2330"}
    )

    assert result == {
        "scenario": "technical_valuation_support_risk",
        "preview_rendered": True,
    }


def test_phase_b_audit_returns_full_compacted_packet_without_raw_model_text(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_service,
        "_execute_live_canonical_shadow_sample",
        lambda **_kwargs: {
            "response": {
                "request_id": "phase-b-1",
                "result": {
                    "analysis_id": "analysis-1",
                    "snapshot_id": "snapshot-1",
                    "profile": "comprehensive-16k-v1",
                    "canonical_answer_text_hash": "a" * 64,
                    "validator_result": "pass",
                    "validator_reason_codes": ["pass"],
                },
            },
            "candidate": {
                "compacted_packet": {
                    "contract_version": "model-fact-packet-v2",
                    "artifact_identity": {"analysis_id": "analysis-1"},
                    "facts": [{"fact_id": "F001"}],
                    "omissions": [{"scope": "events", "reason": "scan_incomplete"}],
                    "render_contract": {"version": "v1"},
                },
                "packet_token_count": 1200,
                "estimated_prompt_token_count": 1300,
                "prompt_token_count": 1400,
                "completion_token_count": 100,
                "removed_fact_count": 0,
                "removed_event_count": 0,
                "removed_fact_ids": [],
                "removed_event_ids": [],
                "explanation_blocks": [{"block_type": "fact", "evidence_ids": ["F001"]}],
                "validator_result": "pass",
                "validator_reason_codes": ["pass"],
                "model_output": "must-not-be-returned",
            },
            "preview": {"text": "候選回答", "sha256": "b" * 64},
        },
    )

    result = benchmark_service.run_live_phase_b_audit_sample(
        scenario="fundamental_chip_technical_news",
        code="2330",
    )

    assert result["compacted_model_fact_packet_v2"]["contract_version"] == (
        "model-fact-packet-v2"
    )
    assert result["omitted_sections"] == [
        {"section": "events", "reason": "scan_incomplete"}
    ]
    assert result["actual_model_explanation_blocks"][0]["evidence_ids"] == ["F001"]
    assert result["rendered_answer"] == "候選回答"
    assert result["web_line_canonical_text_hash_match"] is False
    assert result["raw_model_text_returned"] is False
    assert "model_output" not in result


def test_phase_b_audit_api_uses_authenticated_fixed_scenario_boundary(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")
    monkeypatch.setattr(
        benchmark_api,
        "run_live_phase_b_audit_sample",
        lambda **kwargs: {"scenario": kwargs["scenario"], "candidate_reply_sent_to_line": False},
    )

    result = benchmark_api.phase_b_audit_sample(
        {"scenario": "chip_night_us_events", "code": "2330"}
    )

    assert result == {
        "scenario": "chip_night_us_events",
        "candidate_reply_sent_to_line": False,
    }


def test_research_cache_control_primes_real_cache_without_exposing_query(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_service,
        "_benchmark_job",
        lambda **_kwargs: ({"model_facts": {"code": "2330"}}, "固定新聞問題"),
    )
    monkeypatch.setattr(
        benchmark_service,
        "plan_line_request",
        lambda *_args, **_kwargs: {
            "execution_plan": {"effective_scopes": ["current_news"]}
        },
    )
    monkeypatch.setattr(benchmark_service, "clear_line_model_research_cache", lambda: 2)
    monkeypatch.setattr(
        benchmark_service,
        "enrich_shadow_model_facts_with_research",
        lambda **_kwargs: {
            "summary": {
                "cache_state": "miss",
                "query_hashes": ["hash-only"],
                "canonical_table_writes": 0,
            }
        },
    )

    result = benchmark_service.control_live_research_cache(
        scenario="fundamental_chip_technical_news",
        code="2330",
        mode="prime",
    )

    assert result["primed"] is True
    assert result["cleared_entries"] == 2
    assert result["canonical_table_writes"] == 0
    assert result["raw_query_persisted"] is False
    assert "question" not in result


def test_research_cache_control_rejects_nonresearch_scenario(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_service,
        "_benchmark_job",
        lambda **_kwargs: ({"model_facts": {"code": "2330"}}, "固定技術問題"),
    )
    monkeypatch.setattr(
        benchmark_service,
        "plan_line_request",
        lambda *_args, **_kwargs: {"execution_plan": {"effective_scopes": ["technical"]}},
    )

    with pytest.raises(benchmark_service.LineModelBenchmarkError) as captured:
        benchmark_service.control_live_research_cache(
            scenario="focused_technical",
            code="2330",
            mode="prime",
        )

    assert captured.value.reason_code == "research_not_requested"


def test_research_cache_control_api_uses_fixed_scenario_and_mode(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")
    monkeypatch.setattr(
        benchmark_api,
        "control_live_research_cache",
        lambda **kwargs: {"scenario": kwargs["scenario"], "mode": kwargs["mode"]},
    )

    result = benchmark_api.research_cache_control(
        {
            "scenario": "fundamental_chip_technical_news",
            "code": "2330",
            "mode": "clear",
        }
    )

    assert result == {
        "scenario": "fundamental_chip_technical_news",
        "mode": "clear",
    }


def test_cold_load_lifecycle_holds_one_maintenance_slot(monkeypatch) -> None:
    monkeypatch.setenv("LINE_MODEL_COLD_PROBE_ENABLED", "true")
    monkeypatch.setenv("LINE_MODEL_COLD_PROBE_TIMEOUT_SECONDS", "1260")
    monkeypatch.setattr(benchmark_service, "qwen_model_resident", lambda: True)
    monkeypatch.setattr(benchmark_service, "model_admission_snapshot", lambda: {"queue_depth": 0})
    inside_slot = False
    calls = []

    def unload():
        assert inside_slot
        calls.append("unload")
        return True

    def preload(**kwargs):
        assert inside_slot
        assert kwargs["timeout_seconds"] == 1260
        calls.append("preload")
        return {"load_duration_ms": 4500}

    def chat(*args, **kwargs):
        assert inside_slot
        assert kwargs["timeout_seconds"] == 40
        assert not kwargs.get("allow_background_timeout")
        calls.append("generate")
        return "READY"

    def maintenance(callable_, **kwargs):
        nonlocal inside_slot
        assert kwargs == {"category": "maintenance_benchmark_cold_lifecycle", "predicted_duration_ms": 1330000}
        inside_slot = True
        value = callable_()
        inside_slot = False
        return type("Execution", (), {"value": value, "queue_wait_ms": 3, "execution_ms": 4500})()

    monkeypatch.setattr(benchmark_service, "qwen_unload_text_model", unload)
    monkeypatch.setattr(benchmark_service, "qwen_preload_text_model", preload)
    monkeypatch.setattr(benchmark_service, "qwen_chat", chat)
    monkeypatch.setattr(benchmark_service, "run_maintenance_model", maintenance)

    result = benchmark_service.run_live_cold_load_probe(maintenance_window_confirmed=True)

    assert calls == ["unload", "preload", "generate"]
    assert result["resident_before"] is True
    assert result["unloaded"] is True
    assert result["resident_after"] is True
    assert result["maintenance_execution_ms"] == 4500
    assert result["probe_status"] == "pass"
    assert result["valid_for_live_reply_deadline"] is False
    assert result["valid_for_load_matrix"] is False


@pytest.mark.parametrize("enabled,confirmed,reason", [
    (False, True, "cold_probe_disabled"),
    (True, False, "maintenance_window_required"),
])
def test_cold_probe_gates_reject_before_residency_or_unload(monkeypatch, enabled, confirmed, reason):
    monkeypatch.setenv("LINE_MODEL_COLD_PROBE_ENABLED", str(enabled))
    monkeypatch.setattr(benchmark_service, "qwen_model_resident", lambda: pytest.fail("must not access model"))
    with pytest.raises(benchmark_service.LineModelBenchmarkError) as caught:
        benchmark_service.run_live_cold_load_probe(maintenance_window_confirmed=confirmed)
    assert caught.value.reason_code == reason


def test_cold_load_failure_records_recovery_without_retry(monkeypatch):
    monkeypatch.setenv("LINE_MODEL_COLD_PROBE_ENABLED", "true")
    monkeypatch.setattr(benchmark_service, "qwen_model_resident", lambda: False)
    calls = []
    monkeypatch.setattr(benchmark_service, "qwen_unload_text_model", lambda: True)
    def preload(**kwargs):
        calls.append("preload")
        raise benchmark_service.QwenClientError("private detail", reason_code="model_load_timeout")
    monkeypatch.setattr(benchmark_service, "qwen_preload_text_model", preload)
    monkeypatch.setattr(benchmark_service, "qwen_chat", lambda *a, **k: pytest.fail("no generation after failed load"))
    monkeypatch.setattr(benchmark_service, "run_maintenance_model", lambda f, **k: type(
        "Execution", (), {"value": f(), "queue_wait_ms": 0, "execution_ms": 1260000})())
    monkeypatch.setattr(benchmark_service, "model_admission_snapshot", lambda: {})
    result = benchmark_service.run_live_cold_load_probe(maintenance_window_confirmed=True)
    assert calls == ["preload"]
    assert result["probe_status"] == "fail"
    assert result["failure_reason_code"] == "model_load_timeout"
    assert result["recovery_required"] is True
    assert "private detail" not in str(result)


def test_cold_capability_is_read_only_and_disabled_by_default(monkeypatch):
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")
    monkeypatch.delenv("LINE_MODEL_COLD_PROBE_ENABLED", raising=False)
    monkeypatch.setattr(benchmark_api, "run_live_cold_load_probe", lambda **k: pytest.fail("GET must not load"))
    result = benchmark_api.cold_load_capability()
    assert result["enabled"] is False
    assert result["exclusive_admission"] is True
    assert result["benchmark_contract"] == "line-model-maintenance-cold-load-v2"


def test_cold_api_translates_disabled_to_explicit_conflict(monkeypatch):
    monkeypatch.setenv("LINE_MODEL_BENCHMARK_ENABLED", "true")
    monkeypatch.delenv("LINE_MODEL_COLD_PROBE_ENABLED", raising=False)
    with pytest.raises(HTTPException) as caught:
        benchmark_api.cold_load_probe({"acknowledge_possible_interactive_delay": True, "maintenance_window_confirmed": True})
    assert caught.value.status_code == 409
    assert caught.value.detail["reason_code"] == "cold_probe_disabled"


def test_vision_stable_reply_probe_uses_production_fallback_path(monkeypatch) -> None:
    class Executor:
        def __init__(self) -> None:
            self.shutdown_called = False

        def shutdown(self, *, wait: bool) -> None:
            self.shutdown_called = wait

    executor = Executor()
    vision = object()
    monkeypatch.setattr(
        benchmark_service,
        "_start_vision_probe",
        lambda: ("qwen3-vl:8b-instruct", executor, vision),
    )
    monkeypatch.setattr(
        benchmark_service,
        "run_live_stable_reply_sample",
        lambda **_kwargs: {
            "answer_path": "admission_fallback:predicted_deadline_admission_rejected",
            "completed_within_internal_reply_budget": True,
            "candidate_reply_submitted": False,
        },
    )
    monkeypatch.setattr(
        benchmark_service,
        "_vision_outcome",
        lambda _future: ("completed", ""),
    )
    monkeypatch.setattr(benchmark_service, "qwen_model_resident", lambda *_args: True)
    monkeypatch.setattr(benchmark_service, "model_admission_snapshot", lambda: {"queue_depth": 0})

    result = benchmark_service.run_live_vision_stable_reply_probe()

    assert result["stable_reply"]["completed_within_internal_reply_budget"] is True
    assert result["stable_reply"]["candidate_reply_submitted"] is False
    assert result["vision_outcome"] == "completed"
    assert result["vision_outcome_at_stable_reply"] == "completed"
    assert executor.shutdown_called is True


def test_vision_outcome_records_pending_future_without_raising() -> None:
    class PendingVision:
        def result(self, *, timeout: int) -> None:
            assert timeout == 5
            raise benchmark_service.FutureTimeoutError()

    assert benchmark_service._vision_outcome(PendingVision()) == (
        "still_running",
        "vision_completion_pending",
    )


def test_memory_warmup_failure_is_classified_for_benchmark_api(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_service,
        "qwen_model_resident",
        lambda model_id=None, **_kwargs: model_id is None,
    )
    monkeypatch.setattr(
        benchmark_service,
        "run_maintenance_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            benchmark_service.QwenClientError("timeout")
        ),
    )

    with pytest.raises(benchmark_service.LineModelBenchmarkError) as caught:
        benchmark_service.run_live_memory_contention_probe()

    assert caught.value.reason_code == "memory_model_warmup_failed"


def test_vision_warmup_failure_is_classified_for_benchmark_api(monkeypatch) -> None:
    monkeypatch.setattr(benchmark_service, "qwen_model_resident", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        benchmark_service,
        "run_maintenance_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            benchmark_service.QwenClientError("timeout")
        ),
    )

    with pytest.raises(benchmark_service.LineModelBenchmarkError) as caught:
        benchmark_service._ensure_vision_model_resident()

    assert caught.value.reason_code == "vision_model_warmup_failed"


def test_benchmark_job_reuses_read_only_market_api_projection(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_fetch(code: str, **kwargs: object) -> dict[str, object]:
        captured["code"] = code
        captured["analysis_mode"] = kwargs.get("analysis_mode")
        return {"code": code, "trade_date": "2026-08-28"}

    def fake_projection(payload: dict[str, object]) -> dict[str, object]:
        captured["question"] = payload.get("_line_question")
        return {"code": payload["code"], "approved": True}

    monkeypatch.setattr(benchmark_service, "fetch_daily_market_data", fake_fetch)
    monkeypatch.setattr(benchmark_service, "build_line_model_fact_projection", fake_projection)

    job, question = benchmark_service._benchmark_job(
        scenario="technical_valuation_support_risk",
        code="2330",
    )

    assert captured == {
        "code": "2330",
        "analysis_mode": "close_batch",
        "question": question,
    }
    assert job["model_facts"] == {"code": "2330", "approved": True}
    assert job["question"] == question
    assert set(job["_benchmark_stage_timings_ms"]) == {"retrieval", "projection"}
    assert all(value >= 0 for value in job["_benchmark_stage_timings_ms"].values())


def test_benchmark_has_fixed_focused_and_comprehensive_workloads(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_service,
        "fetch_daily_market_data",
        lambda *_args, **_kwargs: {"code": "2330", "trade_date": "2026-08-28"},
    )
    monkeypatch.setattr(
        benchmark_service,
        "build_line_model_fact_projection",
        lambda payload: {"code": payload["code"]},
    )

    focused_job, _ = benchmark_service._benchmark_job(
        scenario="focused_valuation",
        code="2330",
    )
    comprehensive_job, _ = benchmark_service._benchmark_job(
        scenario="fundamental_chip_technical_news",
        code="2330",
    )

    from services.line_request_planning_service import plan_line_request

    assert plan_line_request(
        focused_job["question"], focus="overview", has_stock=True
    )["execution_plan"]["effective_depth"] == "focused"
    assert plan_line_request(
        comprehensive_job["question"], focus="overview", has_stock=True
    )["execution_plan"]["effective_depth"] == "comprehensive"


def test_live_acceptance_summary_keeps_reject_rate_with_first_pass_evidence() -> None:
    attempts = [
        {
            "scenario": "scenario-a",
            "body": {
                "queue_wait_ms": 10,
                "execution_ms": 100,
                "result": {"validator_result": "pass", "validator_reason_codes": []},
            },
        },
        {
            "scenario": "scenario-a",
            "body": {
                "queue_wait_ms": 20,
                "execution_ms": 200,
                "result": {
                    "validator_result": "reject",
                    "validator_reason_codes": ["invalid_json"],
                },
            },
        },
    ]

    summary = acceptance_runner._summary(attempts, mode="phase-b")

    assert summary["attempts_by_scenario"]["scenario-a"] == {
        "attempt_count": 2,
        "pass_count": 1,
        "reject_count": 1,
        "reject_rate": 0.5,
    }


def test_live_preemption_summary_reports_interactive_wait_p95() -> None:
    attempts = [
        {
            "scenario": "scenario-a",
            "body": {
                "shadow_outcome": "preempted",
                "interactive_queue_wait_ms": 2000,
                "interactive_total_wait_ms": 2300,
            },
        },
        {
            "scenario": "scenario-b",
            "body": {
                "shadow_outcome": "preempted",
                "interactive_queue_wait_ms": 2100,
                "interactive_total_wait_ms": 2400,
            },
        },
    ]

    summary = acceptance_runner._summary(attempts, mode="preemption")

    assert summary["preemption_success_rate"] == 1.0
    assert summary["interactive_queue_wait_p95_ms"] == 2100
    assert summary["interactive_total_wait_p95_ms"] == 2400
