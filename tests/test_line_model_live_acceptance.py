from __future__ import annotations

import importlib.util
import sys
import time
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_line_model_live_acceptance.py"
SPEC = importlib.util.spec_from_file_location("run_line_model_live_acceptance", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _row(observed_at: str, *, line_ready: bool = True) -> dict:
    return {
        "observed_at": observed_at,
        "services": {
            "market_8010": {"ready": True},
            "ollama_8020": {"ready": True},
            "line_8021": {"ready": line_ready},
        },
    }


def test_health_summary_requires_measured_coverage_of_attempt_window() -> None:
    summary = runner._summarize_health_rows(
        [
            _row("2026-08-30T04:00:00.000+08:00"),
            _row("2026-08-30T04:01:00.000+08:00"),
        ],
        attempt_started_at="2026-08-30T04:00:01.000+08:00",
        attempt_finished_at="2026-08-30T04:00:59.000+08:00",
    )

    assert summary["attempt_window_covered"] is True
    assert summary["all_services_ready"] is True
    assert summary["not_ready_by_service"] == {
        "market_8010": 0,
        "ollama_8020": 0,
        "line_8021": 0,
    }


def test_health_summary_exposes_any_line_service_drop() -> None:
    summary = runner._summarize_health_rows(
        [
            _row("2026-08-30T04:00:00.000+08:00"),
            _row("2026-08-30T04:00:30.000+08:00", line_ready=False),
            _row("2026-08-30T04:01:00.000+08:00"),
        ],
        attempt_started_at="2026-08-30T04:00:01.000+08:00",
        attempt_finished_at="2026-08-30T04:00:59.000+08:00",
    )

    assert summary["attempt_window_covered"] is True
    assert summary["all_services_ready"] is False
    assert summary["not_ready_by_service"]["line_8021"] == 1


def test_health_summary_rejects_uncovered_attempt_window() -> None:
    summary = runner._summarize_health_rows(
        [
            _row("2026-08-30T04:00:02.000+08:00"),
            _row("2026-08-30T04:00:58.000+08:00"),
        ],
        attempt_started_at="2026-08-30T04:00:01.000+08:00",
        attempt_finished_at="2026-08-30T04:00:59.000+08:00",
    )

    assert summary["attempt_window_covered"] is False


def test_source_binding_covers_final_packet_and_line_execution_path() -> None:
    required = {
        "scripts/run_line_model_live_acceptance.py",
        "review_src/core/line_model_output_schema.py",
        "review_src/services/line_request_planning_service.py",
        "review_src/services/line_model_benchmark_service.py",
        "review_src/services/model_admission_service.py",
        "review_src/services/line_bot_service.py",
        "review_src/services/line_reply_telemetry_service.py",
        "review_src/api/line_webhook.py",
    }

    assert required.issubset(set(runner.SOURCE_BINDINGS))


def test_collector_rejects_old_or_different_generation_contract():
    from core.line_model_output_schema import MODEL_OUTPUT_SCHEMA_VERSION, model_analysis_output_schema
    from services.line_model_shadow_service import (
        MODEL_ANALYSIS_SYSTEM_PROMPT,
        MODEL_ANALYSIS_FINAL_GUARD,
        _generation_request_guard,
    )

    schema = json.dumps(model_analysis_output_schema("comprehensive"), ensure_ascii=False, separators=(",", ":"))
    scopes = ["technical", "valuation"]
    prompt = (
        f"{MODEL_ANALYSIS_SYSTEM_PROMPT}\nOUTPUT_JSON_SCHEMA：{schema}\n"
        f"{MODEL_ANALYSIS_FINAL_GUARD}{_generation_request_guard(scopes)}"
    )
    current = {
        "depth": "comprehensive", "generation_schema_version": MODEL_OUTPUT_SCHEMA_VERSION,
        "scopes": scopes,
        "generation_schema_sha256": hashlib.sha256(schema.encode("utf-8")).hexdigest(),
        "generation_prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }
    assert runner._generation_contract_matches(current) is True
    assert runner._generation_contract_matches({}) is False
    assert runner._generation_contract_matches({**current, "generation_prompt_sha256": "old"}) is False
    assert runner._generation_contract_matches({**current, "generation_schema_sha256": "old"}) is False


def test_collector_persists_first_mismatch_then_stops_without_more_model_calls(monkeypatch, tmp_path):
    output = tmp_path / "audit.json"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--mode", "phase-a", "--count", "20", "--output", str(output)])
    monkeypatch.setenv("BOT_MARKET_DATA_TOKEN", "test-value")
    monkeypatch.setattr(runner, "dotenv_values", lambda _path: {"BOT_MARKET_DATA_TOKEN": "test-only" * 8})
    monkeypatch.setattr(runner, "_hardware_snapshot", lambda: {})
    monkeypatch.setattr(runner, "_health_row", lambda: _row(runner._now()))
    monkeypatch.setattr(runner, "_summarize_health_rows", lambda *_args, **_kwargs: {
        "all_services_ready": True, "attempt_window_covered": True,
        "not_ready_by_service": {"market_8010": 0, "ollama_8020": 0, "line_8021": 0},
    })
    calls = []

    def post(*_args):
        calls.append(True)
        return 200, {"result": {"validator_result": "pass", "validator_reason_codes": []}}

    monkeypatch.setattr(runner, "_post", post)
    assert runner.main() == 2
    assert len(calls) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["generation_contract_verified"] is False
    assert report["summary"]["attempt_count"] == 1
    assert len(output.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_health_monitor_start_waits_for_first_persisted_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runner, "_health_row", lambda: _row("2026-08-30T04:00:00.000+08:00"))
    monitor = runner._HealthEvidenceMonitor(tmp_path / "health.jsonl")

    started = time.monotonic()
    monitor.start()
    try:
        assert monitor.rows
        assert monitor.path.is_file()
        assert time.monotonic() - started < 1
    finally:
        monitor.stop()
