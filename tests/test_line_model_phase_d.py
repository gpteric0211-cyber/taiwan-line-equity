from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import run_line_model_phase_d as phase_d  # noqa: E402


def _attempt(*, queue_ms: int, generation_ms: int, validator: str = "pass") -> dict:
    return {
        "http_status": 200,
        "http_round_trip_ms": queue_ms + generation_ms + 20,
        "body": {
            "queue_wait_ms": queue_ms,
            "benchmark_end_to_end_ms": queue_ms + generation_ms + 10,
            "result": {
                "candidate_model_called": True,
                "validator_result": validator,
                "error_classification": None,
                "ungrounded_claim_count": 0,
                "referee_override_count": 0,
                "stage_timings_ms": {
                    "retrieval": 4.0,
                    "projection": 1.0,
                    "classification": 0.1,
                    "packet_build": 2.0,
                    "generation": float(generation_ms),
                    "validation": 0.2,
                    "render": 0.1,
                },
            },
        },
    }


def test_phase_d_summary_keeps_stage_p95_and_does_not_fake_line_reply_latency() -> None:
    summary = phase_d._measurement_summary(
        [
            _attempt(queue_ms=100, generation_ms=6000),
            _attempt(queue_ms=200, generation_ms=7000, validator="reject"),
        ],
        reply_budget_ms=45_000,
        work_stop_ms=31_000,
    )

    assert summary["completed_samples"] == 2
    assert summary["queue_wait_p95_ms"] == 200
    assert summary["generation_p95_ms"] == 7000
    assert summary["validation_p95_ms"] == 0.2
    assert summary["render_p95_ms"] == 0.1
    assert summary["candidate_work_stop_gate_pass"] is True
    assert summary["actual_webhook_ingress_to_line_reply_p95_ms"] is None
    assert summary["actual_line_reply_gate_status"] == "pending_real_line_delivery_telemetry"


def test_phase_d_aggregate_counts_only_qualified_trading_days(tmp_path: Path) -> None:
    for day, qualifies, focused, comprehensive in (
        ("2026-08-28", True, 20, 21),
        ("2026-08-29", False, 99, 99),
        ("2026-08-31", True, 22, 23),
    ):
        folder = tmp_path / day
        folder.mkdir()
        (folder / "daily_manifest.json").write_text(
            json.dumps(
                {
                    "trading_day": {
                        "local_date": day,
                        "qualifies_as_trading_day": qualifies,
                    },
                    "completed_samples_by_profile": {
                        "focused": focused,
                        "comprehensive": comprehensive,
                    },
                    "line_reply_telemetry": {
                        "sent_count": 2 if qualifies else 99,
                        "shadow_active_at_handler_start_count": 1 if day == "2026-08-31" else 0,
                        "webhook_ingress_to_reply_p95_ms": 500 if qualifies else 9999,
                    },
                }
            ),
            encoding="utf-8",
        )

    result = phase_d._aggregate(tmp_path)

    assert result["trading_day_count"] == 2
    assert result["completed_samples_by_profile"] == {
        "comprehensive": 44,
        "focused": 42,
    }
    assert result["five_trading_days_pass"] is False
    assert result["phase_d_sample_gate_pass"] is False
    assert result["actual_line_reply_sent_samples"] == 4
    assert result["actual_line_reply_shadow_active_samples"] == 1
    assert result["maximum_daily_webhook_ingress_to_reply_p95_ms"] == 500
    assert result["actual_line_reply_gate_status"] == "observed_with_shadow_contention"


def test_phase_d_weekend_is_diagnostic_not_trading_day() -> None:
    status = phase_d._trading_day_status(phase_d.date.fromisoformat("2026-08-30"))

    assert status["qualifies_as_trading_day"] is False
    assert status["reason"] == "weekend"


def test_phase_d_summarizes_deidentified_real_line_reply_telemetry(monkeypatch, tmp_path: Path) -> None:
    telemetry = tmp_path / "reply.jsonl"
    rows = [
        {
            "captured_at": "2026-08-31T09:00:00+08:00",
            "reply_status": "sent",
            "webhook_ingress_to_reply_ms": 700,
            "line_send_ms": 90,
            "shadow_active_at_handler_start": False,
        },
        {
            "captured_at": "2026-08-31T09:01:00+08:00",
            "reply_status": "sent",
            "webhook_ingress_to_reply_ms": 2100,
            "line_send_ms": 120,
            "shadow_active_at_handler_start": True,
            "candidate_selected": True,
            "candidate_delivered": False,
        },
        {
            "captured_at": "2026-08-30T09:00:00+08:00",
            "reply_status": "sent",
            "webhook_ingress_to_reply_ms": 9999,
            "line_send_ms": 999,
            "shadow_active_at_handler_start": True,
        },
    ]
    telemetry.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LINE_REPLY_TELEMETRY_PATH", str(telemetry))

    result = phase_d._line_reply_telemetry_summary(date.fromisoformat("2026-08-31"))

    assert result["sent_count"] == 2
    assert result["shadow_active_at_handler_start_count"] == 1
    assert result["canary_selected_count"] == 1
    assert result["canary_delivered_count"] == 0
    assert result["webhook_ingress_to_reply_p95_ms"] == 2100
    assert result["shadow_active_webhook_ingress_to_reply_p95_ms"] == 2100
    assert result["canary_selected_webhook_ingress_to_reply_p95_ms"] == 2100
    assert result["gate_status"] == "observed"


def _research_attempt(cache_state: str, retrieval_mode: str) -> dict:
    return {
        "body": {
            "result": {
                "research_summary": {
                    "query_count": 1,
                    "cache_state": cache_state,
                    "retrieval_mode": retrieval_mode,
                }
            }
        }
    }


def test_phase_d_news_cache_hit_requires_every_research_sample_to_hit() -> None:
    passed = phase_d._news_cache_assertion(
        [_research_attempt("hit", "cache_only"), _research_attempt("hit", "cache_only")],
        "hit",
    )
    failed = phase_d._news_cache_assertion(
        [_research_attempt("hit", "cache_only"), _research_attempt("miss", "bounded_live")],
        "hit",
    )

    assert passed["matches"] is True
    assert failed["matches"] is False


def test_phase_d_news_cache_miss_requires_observed_bounded_live_miss() -> None:
    result = phase_d._news_cache_assertion(
        [_research_attempt("miss", "bounded_live"), _research_attempt("hit", "cache_only")],
        "miss",
    )

    assert result["matches"] is True
    assert result["reason"] == "at_least_one_bounded_live_miss"


def _canonical_attempt(*, cache: str, news: str) -> dict:
    return {
        "body": {
            "result": {
                "canonical_artifact_cache_state": cache,
                "canonical_news_state": news,
            }
        }
    }


def test_phase_d_canonical_cache_and_news_states_use_final_packet_path() -> None:
    attempts = [
        _canonical_attempt(cache="hit", news="miss"),
        _canonical_attempt(cache="hit", news="miss"),
    ]
    cache = phase_d._canonical_state_assertion(
        attempts,
        "hit",
        field="canonical_artifact_cache_state",
    )
    news = phase_d._canonical_state_assertion(
        attempts,
        "miss",
        field="canonical_news_state",
    )

    assert cache["matches"] is True
    assert news["matches"] is True
    assert cache["canonical_path"] is True
    assert news["canonical_path"] is True


def test_phase_d_canonical_state_requirement_fails_on_mixed_samples() -> None:
    result = phase_d._canonical_state_assertion(
        [
            _canonical_attempt(cache="hit", news="hit"),
            _canonical_attempt(cache="miss", news="hit"),
        ],
        "hit",
        field="canonical_artifact_cache_state",
    )

    assert result["matches"] is False
    assert result["observed_states"] == ["hit", "miss"]


def test_phase_d_health_evidence_reports_midrun_outage(tmp_path: Path) -> None:
    path = tmp_path / "health.jsonl"
    rows = [
        {
            "observed_at": "2026-08-31T09:00:00+08:00",
            "services": {
                "market_8010": {"ready": True},
                "ollama_8020": {"ready": True},
                "line_8021": {"ready": True},
            },
        },
        {
            "observed_at": "2026-08-31T09:00:01+08:00",
            "services": {
                "market_8010": {"ready": True},
                "ollama_8020": {"ready": True},
                "line_8021": {"ready": False},
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = phase_d._health_evidence_summary(path)

    assert result["sample_count"] == 2
    assert result["not_ready_by_service"]["line_8021"] == 1
    assert result["all_services_ready"] is False


def test_service_health_requires_line_payload_ready(monkeypatch) -> None:
    class Response:
        status_code = 200
        content = b"{}"

        def __init__(self, url: str) -> None:
            self.url = url

        def json(self) -> dict:
            if self.url.endswith(":8021/healthz"):
                return {
                    "ready": False,
                    "conversation_memory_ready": False,
                    "line_channel_secret_configured": True,
                    "line_access_token_configured": True,
                    "market_api_token_configured": True,
                    "qwen_base_url_configured": True,
                    "qwen_vision_configured": True,
                    "signature_verification_enabled": True,
                    "read_only_asserted": True,
                    "trading_disabled": True,
                    "decision_ready_required": True,
                    "model_recalculation_disabled": True,
                }
            return {}

    monkeypatch.setattr(phase_d.requests, "get", lambda url, **_kwargs: Response(url))

    with pytest.raises(RuntimeError, match="all services healthy"):
        phase_d._service_health()


def test_ollama_snapshot_records_text_and_vision_residency(monkeypatch) -> None:
    class Response:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def json(self) -> dict:
            return self.payload

    payloads = {
        "/api/version": {"version": "0.33.1"},
        "/api/ps": {
            "models": [
                {
                    "name": "taiwan-stock-qwen:latest",
                    "digest": "text-digest",
                    "context_length": 16384,
                    "size_vram": 19_000,
                    "details": {"parameter_size": "26.9B", "quantization_level": "Q4_K_M"},
                },
                {
                    "name": "qwen3-vl:8b-instruct",
                    "digest": "vision-digest",
                    "context_length": 16384,
                    "size_vram": 7_000,
                    "details": {"parameter_size": "8.8B", "quantization_level": "Q4_K_M"},
                },
            ]
        },
        "/api/tags": {"models": []},
    }

    def fake_get(url: str, **_kwargs: object) -> Response:
        return Response(next(value for suffix, value in payloads.items() if url.endswith(suffix)))

    monkeypatch.setattr(phase_d.requests, "get", fake_get)
    monkeypatch.setattr(phase_d, "_last_log_match", lambda _pattern: None)
    monkeypatch.setenv("QWEN_MODEL_ID", "taiwan-stock-qwen")
    monkeypatch.setenv("QWEN_VISION_MODEL_ID", "qwen3-vl:8b-instruct")

    _profile, runtime = phase_d._ollama_snapshot()

    assert runtime["text_model_resident_at_snapshot"] is True
    assert runtime["vision_model_resident_at_snapshot"] is True
    assert runtime["vision_model_size_vram_bytes"] == 7_000
    assert runtime["running_model_count"] == 2


def test_environment_snapshot_persists_residency_and_nvidia_cuda(monkeypatch) -> None:
    monkeypatch.setattr(
        phase_d,
        "_nvidia_snapshot",
        lambda: {
            "gpu_vendor_model": "NVIDIA GeForce RTX 5090",
            "driver_version": "610.88",
            "gpu_vram_bytes": 32_607 * 1024 * 1024,
            "cuda_version": "13.3",
        },
    )
    monkeypatch.setattr(
        phase_d,
        "_ollama_snapshot",
        lambda: (
            {"model_id": "taiwan-stock-qwen:latest"},
            {
                "cuda_version": "unverified",
                "running_model_count": 2,
                "text_model_resident_at_snapshot": True,
                "vision_model_resident_at_snapshot": True,
            },
        ),
    )
    monkeypatch.setattr(phase_d, "_cpu_model", lambda: "CPU")
    monkeypatch.setattr(phase_d, "_system_memory_bytes", lambda: 64_000)

    deployment, profile, match = phase_d._environment_snapshot("610.88")

    assert deployment["cuda_version"] == "13.3"
    assert profile["runtime_residency"]["running_model_count"] == 2
    assert profile["runtime_residency"]["vision_model_resident_at_snapshot"] is True
    assert match["driver_expectation_matches"] is True
