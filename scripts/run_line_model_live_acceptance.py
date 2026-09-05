from __future__ import annotations

"""Collect final-path LINE model evidence through the live 8021 controller."""

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ENV = PROJECT_ROOT / ".env.line_bot"
BASE_URL = "http://127.0.0.1:8021/internal/line-model-benchmark"
SCENARIOS = (
    "fundamental_chip_technical_news",
    "technical_valuation_support_risk",
    "chip_night_us_events",
)
HEALTH_ENDPOINTS = {
    "market_8010": "http://127.0.0.1:8010/healthz",
    "ollama_8020": "http://127.0.0.1:8020/api/version",
    "line_8021": "http://127.0.0.1:8021/healthz",
}
SOURCE_BINDINGS = (
    "scripts/run_line_model_live_acceptance.py",
    "review_src/core/line_model_contract.py",
    "review_src/core/line_model_validation.py",
    "review_src/core/line_model_output_schema.py",
    "review_src/core/line_model_release_config.py",
    "review_src/core/news_research_policy.py",
    "review_src/core/public_url.py",
    "review_src/adapter/controlled_news_research.py",
    "review_src/adapter/qwen_local.py",
    "review_src/adapter/bot_market_data_client.py",
    "review_src/repository/single_track_v3_repository.py",
    "review_src/services/canonical_analysis_orchestrator.py",
    "review_src/services/canonical_question_analysis_service.py",
    "review_src/services/canonical_model_packet_service.py",
    "review_src/services/canonical_model_packet_orchestrator.py",
    "review_src/services/canonical_model_candidate_service.py",
    "review_src/services/line_canonical_model_service.py",
    "review_src/services/line_model_candidate_delivery_service.py",
    "review_src/services/line_model_candidate_reply_service.py",
    "review_src/services/line_model_research_service.py",
    "review_src/services/line_request_planning_service.py",
    "review_src/services/line_model_shadow_service.py",
    "review_src/services/line_model_benchmark_service.py",
    "review_src/services/model_admission_service.py",
    "review_src/services/line_bot_service.py",
    "review_src/services/line_reply_telemetry_service.py",
    "review_src/api/line_model_benchmark.py",
    "review_src/api/line_webhook.py",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _post(session: requests.Session, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
    response = session.post(f"{BASE_URL}/{path}", json=payload, timeout=150)
    try:
        body: Any = response.json()
    except ValueError:
        body = {"non_json_response_sha256": hashlib.sha256(response.content).hexdigest()}
    return response.status_code, body


def _probe_health(name: str, url: str, *, timeout_seconds: float = 0.4) -> dict[str, Any]:
    started = time.monotonic()
    try:
        response = requests.get(url, timeout=timeout_seconds)
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        ready = response.status_code == 200
        if name == "market_8010":
            ready = ready and payload.get("status") == "ok"
        elif name == "line_8021":
            ready = ready and payload.get("status") == "ok" and payload.get("ready") is True
        return {
            "ready": bool(ready),
            "http_status": int(response.status_code),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except requests.RequestException as exc:
        return {
            "ready": False,
            "http_status": None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error_class": type(exc).__name__,
        }


def _health_row() -> dict[str, Any]:
    return {
        "observed_at": _now(),
        "monotonic_ns": time.monotonic_ns(),
        "services": {
            name: _probe_health(name, url)
            for name, url in HEALTH_ENDPOINTS.items()
        },
    }


def _summarize_health_rows(
    rows: list[dict[str, Any]],
    *,
    attempt_started_at: str | None,
    attempt_finished_at: str | None,
) -> dict[str, Any]:
    not_ready_by_service = {
        name: sum(
            1
            for row in rows
            if not bool((row.get("services") or {}).get(name, {}).get("ready"))
        )
        for name in HEALTH_ENDPOINTS
    }
    first_observed_at = str(rows[0].get("observed_at") or "") if rows else None
    last_observed_at = str(rows[-1].get("observed_at") or "") if rows else None
    attempt_window_covered = bool(
        rows
        and attempt_started_at
        and attempt_finished_at
        and first_observed_at
        and last_observed_at
        and first_observed_at <= attempt_started_at
        and last_observed_at >= attempt_finished_at
    )
    return {
        "sample_count": len(rows),
        "first_observed_at": first_observed_at,
        "last_observed_at": last_observed_at,
        "attempt_started_at": attempt_started_at,
        "attempt_finished_at": attempt_finished_at,
        "attempt_window_covered": attempt_window_covered,
        "not_ready_by_service": not_ready_by_service,
        "all_services_ready": bool(rows) and not any(not_ready_by_service.values()),
        "probe_interval_seconds": 0.5,
        "probe_timeout_seconds": 0.4,
    }


class _HealthEvidenceMonitor:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._stop = threading.Event()
        self._first_row_recorded = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="line-acceptance-health-monitor",
            daemon=True,
        )
        self.rows: list[dict[str, Any]] = []

    def start(self) -> None:
        self._thread.start()
        if not self._first_row_recorded.wait(timeout=3):
            raise RuntimeError("health evidence monitor did not record its first row")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("health evidence monitor did not stop cleanly")

    def _record(self, handle: Any) -> None:
        row = _health_row()
        self.rows.append(row)
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        self._first_row_recorded.set()

    def _run(self) -> None:
        with self.path.open("x", encoding="utf-8", newline="\n") as handle:
            self._record(handle)
            while not self._stop.wait(0.5):
                self._record(handle)
            self._record(handle)


def _hardware_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    try:
        command = [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ]
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        fields = [item.strip() for item in completed.stdout.splitlines()[0].split(",")]
        snapshot["gpu_name"] = fields[0]
        snapshot["driver_version"] = fields[1]
        snapshot["vram_mib"] = int(fields[2])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        snapshot["nvidia_smi"] = "unavailable"
    try:
        version = requests.get("http://127.0.0.1:8020/api/version", timeout=5).json()
        tags = requests.get("http://127.0.0.1:8020/api/tags", timeout=10).json()
        snapshot["ollama_version"] = version.get("version")
        for item in tags.get("models", []):
            name = str(item.get("name") or item.get("model") or "")
            if name.removesuffix(":latest") == "taiwan-stock-qwen":
                snapshot["model_name"] = name
                snapshot["model_digest"] = item.get("digest")
                break
    except (requests.RequestException, ValueError, AttributeError):
        snapshot["ollama_metadata"] = "unavailable"
    snapshot["context_tokens"] = int(os.getenv("QWEN_CONTEXT_TOKENS", "16384"))
    snapshot["source_hashes"] = {
        relative: _sha256(PROJECT_ROOT / relative) for relative in SOURCE_BINDINGS
    }
    return snapshot


def _summary(attempts: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    if mode == "preemption":
        outcomes = Counter()
        queue_values: list[int] = []
        total_values: list[int] = []
        for attempt in attempts:
            body = attempt.get("body") if isinstance(attempt.get("body"), dict) else {}
            outcomes[str(body.get("shadow_outcome") or "http_error")] += 1
            if body.get("interactive_queue_wait_ms") is not None:
                queue_values.append(int(body["interactive_queue_wait_ms"]))
            if body.get("interactive_total_wait_ms") is not None:
                total_values.append(int(body["interactive_total_wait_ms"]))
        return {
            "mode": mode,
            "attempt_count": len(attempts),
            "shadow_outcome_counts": dict(outcomes),
            "preemption_success_count": outcomes.get("preempted", 0),
            "preemption_success_rate": round(
                outcomes.get("preempted", 0) / len(attempts), 4
            )
            if attempts
            else None,
            "interactive_queue_wait_p95_ms": _percentile(queue_values, 0.95),
            "interactive_total_wait_p95_ms": _percentile(total_values, 0.95),
            "valid_for_load_matrix": False,
            "load_matrix_status": "single-interactive-preemption-only",
        }
    validator = Counter()
    errors = Counter()
    by_scenario: dict[str, Counter[str]] = defaultdict(Counter)
    queue_values: list[int] = []
    execution_values: list[int] = []
    context_400 = 0
    preempted = 0
    for attempt in attempts:
        scenario = str(attempt.get("scenario") or "")
        body = attempt.get("body") if isinstance(attempt.get("body"), dict) else {}
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        outcome = str(result.get("validator_result") or "http_error")
        validator[outcome] += 1
        by_scenario[scenario][outcome] += 1
        error = str(result.get("error_classification") or "")
        if error:
            errors[error] += 1
        reasons = list(result.get("validator_reason_codes") or [])
        context_400 += int(error == "context_http_400" or "context_http_400" in reasons)
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
        if detail.get("reason_code") == "shadow_preempted_by_interactive":
            preempted += 1
        if body.get("queue_wait_ms") is not None:
            queue_values.append(int(body["queue_wait_ms"]))
        if body.get("execution_ms") is not None:
            execution_values.append(int(body["execution_ms"]))
    per_scenario: dict[str, Any] = {}
    for scenario, counts in by_scenario.items():
        total = sum(counts.values())
        rejected = total - counts.get("pass", 0)
        per_scenario[scenario] = {
            "attempt_count": total,
            "pass_count": counts.get("pass", 0),
            "reject_count": rejected,
            "reject_rate": round(rejected / total, 4) if total else None,
        }
    return {
        "mode": mode,
        "attempt_count": len(attempts),
        "validator_counts": dict(validator),
        "error_classification_counts": dict(errors),
        "context_http_400_count": context_400,
        "preempted_count": preempted,
        "queue_wait_p95_ms": _percentile(queue_values, 0.95),
        "execution_p95_ms": _percentile(execution_values, 0.95),
        "attempts_by_scenario": per_scenario,
        "selection_bias_warning": (
            "Any first-pass sample must be reviewed together with attempt_count, "
            "pass_count, reject_count, and reject_rate in this same artifact."
        ),
        "valid_for_load_matrix": False,
        "load_matrix_status": "not_started_by_this_runner",
    }


def _generation_contract_matches(result: dict[str, Any]) -> bool:
    """Attest the actual generation prompt/shape, not all loaded source code."""
    if not isinstance(result, dict):
        return False
    # Pure imports/construction: no model, DB or external calls.
    import sys
    if str(PROJECT_ROOT / "review_src") not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT / "review_src"))
    from core.line_model_output_schema import MODEL_OUTPUT_SCHEMA_VERSION, model_analysis_output_schema
    from services.line_model_shadow_service import (
        MODEL_ANALYSIS_FINAL_GUARD,
        MODEL_ANALYSIS_SYSTEM_PROMPT,
        _generation_request_guard,
    )

    depth = result.get("depth")
    if depth not in {"focused", "comprehensive"}:
        return False
    scopes = [str(item) for item in result.get("scopes") or [] if str(item)]
    schema = json.dumps(model_analysis_output_schema(depth), ensure_ascii=False, separators=(",", ":"))
    prompt = (
        f"{MODEL_ANALYSIS_SYSTEM_PROMPT}\nOUTPUT_JSON_SCHEMA：{schema}\n"
        f"{MODEL_ANALYSIS_FINAL_GUARD}{_generation_request_guard(scopes)}"
    )
    return (
        result.get("generation_schema_version") == MODEL_OUTPUT_SCHEMA_VERSION
        and result.get("generation_schema_sha256") == hashlib.sha256(schema.encode("utf-8")).hexdigest()
        and result.get("generation_prompt_sha256") == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("phase-a", "phase-b", "preemption"), required=True)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--code", default="2330")
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    raw_output = output.with_suffix(".jsonl")
    health_output = output.with_name(f"{output.stem}_health.jsonl")
    if output.exists() or raw_output.exists() or health_output.exists():
        raise FileExistsError("refusing to overwrite existing acceptance evidence")
    output.parent.mkdir(parents=True, exist_ok=True)

    values = dotenv_values(PRIVATE_ENV)
    token = str(values.get("BOT_MARKET_DATA_TOKEN") or "")
    if len(token) < 32:
        raise RuntimeError("persistent BOT_MARKET_DATA_TOKEN is unavailable")
    os.environ.update({key: str(value) for key, value in values.items() if value is not None})
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    attempts: list[dict[str, Any]] = []
    generation_contract_verified = args.mode == "preemption"
    health_monitor = _HealthEvidenceMonitor(health_output)
    health_monitor.start()
    try:
        with raw_output.open("x", encoding="utf-8", newline="\n") as raw_handle:
            selected_scenarios = (args.scenario,) if args.scenario else SCENARIOS
            for index in range(max(1, args.count)):
                scenario = selected_scenarios[index % len(selected_scenarios)]
                started = _now()
                if args.mode == "preemption":
                    status, body = _post(
                        session,
                        "preemption-probe",
                        {"scenario": scenario, "code": args.code},
                    )
                elif args.mode == "phase-b":
                    status, body = _post(
                        session,
                        "phase-b-audit-sample",
                        {"scenario": scenario, "code": args.code},
                    )
                else:
                    status, body = _post(
                        session,
                        "shadow-sample",
                        {"scenario": scenario, "code": args.code},
                    )
                attempt = {
                    "attempt_index": index + 1,
                    "scenario": scenario,
                    "started_at": started,
                    "finished_at": _now(),
                    "http_status": status,
                    "body": body,
                }
                attempts.append(attempt)
                raw_handle.write(json.dumps(attempt, ensure_ascii=False, separators=(",", ":")) + "\n")
                raw_handle.flush()
                result = body.get("result") if isinstance(body, dict) else {}
                print(
                    json.dumps(
                        {
                            "attempt": index + 1,
                            "scenario": scenario,
                            "http": status,
                            "validator": result.get("validator_result") if isinstance(result, dict) else None,
                            "reasons": result.get("validator_reason_codes") if isinstance(result, dict) else None,
                            "shadow_outcome": body.get("shadow_outcome") if isinstance(body, dict) else None,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if args.mode != "preemption":
                    generation_contract_verified = _generation_contract_matches(result or {})
                    if not generation_contract_verified:
                        print("generation contract mismatch: retained attempt, stopping collection", flush=True)
                        break
    finally:
        health_monitor.stop()

    attempt_started_at = str(attempts[0]["started_at"]) if attempts else None
    attempt_finished_at = str(attempts[-1]["finished_at"]) if attempts else None
    health_summary = _summarize_health_rows(
        health_monitor.rows,
        attempt_started_at=attempt_started_at,
        attempt_finished_at=attempt_finished_at,
    )

    first_pass_by_scenario: dict[str, Any] = {}
    for attempt in attempts:
        body = attempt.get("body") if isinstance(attempt.get("body"), dict) else {}
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        scenario = str(attempt["scenario"])
        if result.get("validator_result") == "pass" and scenario not in first_pass_by_scenario:
            first_pass_by_scenario[scenario] = {
                "attempt_index": attempt["attempt_index"],
                "request_id": body.get("request_id"),
                "full_attempt_is_in": raw_output.name,
                "selected_full_attempt": attempt,
            }
    artifact = {
        "contract": "line-model-live-canonical-acceptance-v2",
        "created_at": _now(),
        "execution_environment": {
            "gateway_8010_online": health_summary["not_ready_by_service"]["market_8010"] == 0,
            "line_8021_online": health_summary["not_ready_by_service"]["line_8021"] == 0,
            "ollama_8020_online": health_summary["not_ready_by_service"]["ollama_8020"] == 0,
            "health_evidence_measured_not_declared": True,
            "health_attempt_window_covered": health_summary["attempt_window_covered"],
            "maintenance_8020_only_mode": False,
            "actual_line_reply_send_exercised": False,
            "uses_live_single_process_admission_controller": True,
            "vision_residency_exercised": False,
            "concurrent_user_traffic_controlled": False,
        },
        "hardware": _hardware_snapshot(),
        "summary": _summary(attempts, mode=args.mode),
        "generation_contract_verified": generation_contract_verified if args.mode != "preemption" else None,
        "generation_binding_scope": "prompt_and_output_schema_only_not_all_runtime_source",
        "first_pass_by_scenario": first_pass_by_scenario,
        "raw_attempts": raw_output.name,
        "health_evidence": health_output.name,
        "health_summary": health_summary,
    }
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(artifact["summary"], ensure_ascii=False, indent=2))
    return 0 if (
        health_summary["all_services_ready"] and health_summary["attempt_window_covered"]
        and generation_contract_verified
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
