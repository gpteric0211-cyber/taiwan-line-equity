from __future__ import annotations

"""Collect hardware-bound Phase D LINE model load evidence.

Each load case writes one ReleaseBenchmarkEvidence document plus an append-only
attempt log. Non-trading-day runs are diagnostics and never advance the five-day
release gate.
"""

import argparse
import ctypes
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ENV = PROJECT_ROOT / ".env.line_bot"
CALENDAR_PATH = PROJECT_ROOT / "review_src" / "data" / "market_calendar" / "twse_holiday_schedule.json"
OLLAMA_LOG = PROJECT_ROOT / "logs" / "line_bot" / "qwen_server.log"
BASE_URL = "http://127.0.0.1:8021/internal/line-model-benchmark"
PHASE_D_ROOT = PROJECT_ROOT / "logs" / "line_model_shadow" / "phase_d"
CONCURRENCY_LEVELS = (1, 2, 4, 8)
PROFILE_SCENARIOS = {
    "focused": ("focused_valuation", "focused_technical"),
    "comprehensive": (
        "fundamental_chip_technical_news",
        "technical_valuation_support_risk",
        "chip_night_us_events",
    ),
}
SOURCE_BINDINGS = (
    "scripts/run_line_model_phase_d.py",
    "review_src/core/release_source_fingerprint.py",
    "review_src/core/line_memory_schema.py",
    "review_src/core/line_model_contract.py",
    "review_src/core/line_model_validation.py",
    "review_src/core/line_model_release_config.py",
    "review_src/core/news_research_policy.py",
    "review_src/core/public_url.py",
    "review_src/core/single_track_v3_schema.py",
    "review_src/adapter/controlled_news_research.py",
    "review_src/adapter/qwen_local.py",
    "review_src/adapter/bot_market_data_client.py",
    "review_src/repository/line_conversation_repository.py",
    "review_src/repository/single_track_v3_repository.py",
    "review_src/services/conversation_memory_service.py",
    "review_src/services/line_model_candidate_reply_service.py",
    "review_src/services/line_model_candidate_delivery_service.py",
    "review_src/services/canonical_question_analysis_service.py",
    "review_src/services/canonical_analysis_orchestrator.py",
    "review_src/services/canonical_model_packet_service.py",
    "review_src/services/canonical_model_packet_orchestrator.py",
    "review_src/services/canonical_model_candidate_service.py",
    "review_src/services/line_canonical_model_service.py",
    "review_src/services/event_safety_scan_service.py",
    "review_src/services/expert_response_renderer.py",
    "review_src/services/stock_entity_registry_service.py",
    "review_src/services/line_model_research_service.py",
    "review_src/services/line_request_planning_service.py",
    "review_src/services/line_model_shadow_service.py",
    "review_src/services/line_model_benchmark_service.py",
    "review_src/services/model_admission_service.py",
    "review_src/services/line_bot_service.py",
    "review_src/services/line_reply_telemetry_service.py",
    "review_src/api/line_model_benchmark.py",
    "review_src/api/bot_market_data.py",
    "review_src/api/line_webhook.py",
)
RESEARCH_CACHE_SCENARIO = "fundamental_chip_technical_news"
HEALTH_ENDPOINTS = {
    "market_8010": "http://127.0.0.1:8010/healthz",
    "ollama_8020": "http://127.0.0.1:8020/api/version",
    "line_8021": "http://127.0.0.1:8021/healthz",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    return round(ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)], 3)


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _trading_day_status(day: date) -> dict[str, Any]:
    closures: set[str] = set()
    calendar_version = "unavailable"
    if CALENDAR_PATH.is_file():
        payload = json.loads(CALENDAR_PATH.read_text(encoding="utf-8"))
        closures = {str(item) for item in payload.get("closure_dates") or []}
        calendar_version = str(payload.get("source_version") or payload.get("updated_at") or "local-cache")
    is_weekday = day.weekday() < 5
    is_closed = day.isoformat() in closures
    qualifies = is_weekday and not is_closed
    return {
        "local_date": day.isoformat(),
        "timezone": "Asia/Taipei",
        "qualifies_as_trading_day": qualifies,
        "reason": "open_weekday" if qualifies else ("weekend" if not is_weekday else "calendar_closure"),
        "calendar_version": calendar_version,
    }


def _system_memory_bytes() -> int | None:
    if os.name != "nt":
        page_size = getattr(os, "sysconf", lambda _key: 0)("SC_PAGE_SIZE")
        pages = getattr(os, "sysconf", lambda _key: 0)("SC_PHYS_PAGES")
        return int(page_size * pages) if page_size and pages else None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    state = MemoryStatus()
    state.length = ctypes.sizeof(MemoryStatus)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        return int(state.total_physical)
    return None


def _cpu_model() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except (OSError, ImportError):
            pass
    return str(platform.processor() or platform.machine() or "unavailable")


def _last_log_match(pattern: str) -> str | None:
    if not OLLAMA_LOG.is_file():
        return None
    size = OLLAMA_LOG.stat().st_size
    with OLLAMA_LOG.open("rb") as handle:
        handle.seek(max(0, size - 512_000))
        text = handle.read().decode("utf-8", errors="replace")
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    if not matches:
        return None
    last = matches[-1]
    return str(last[-1] if isinstance(last, tuple) else last)


def _nvidia_snapshot() -> dict[str, Any]:
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
    fields = [part.strip() for part in completed.stdout.splitlines()[0].split(",")]
    result = {
        "gpu_vendor_model": fields[0],
        "driver_version": fields[1],
        "gpu_vram_bytes": int(fields[2]) * 1024 * 1024,
    }
    try:
        header = subprocess.run(
            ["nvidia-smi"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        cuda_match = re.search(r"CUDA(?: UMD)? Version:\s*([0-9.]+)", header)
        result["cuda_version"] = cuda_match.group(1) if cuda_match else "unverified"
    except (OSError, subprocess.SubprocessError):
        result["cuda_version"] = "unverified"
    return result


def _ollama_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    version = requests.get("http://127.0.0.1:8020/api/version", timeout=5).json()
    running = requests.get("http://127.0.0.1:8020/api/ps", timeout=10).json()
    tags = requests.get("http://127.0.0.1:8020/api/tags", timeout=10).json()
    configured_model = str(os.getenv("QWEN_MODEL_ID", "taiwan-stock-qwen"))
    configured_vision_model = str(os.getenv("QWEN_VISION_MODEL_ID", "qwen3-vl:8b-instruct"))
    running_models = list(running.get("models") or [])
    model = next(
        (
            item
            for item in running_models
            if str(item.get("name") or item.get("model") or "").removesuffix(":latest")
            == configured_model.removesuffix(":latest")
        ),
        None,
    )
    resident_at_snapshot = isinstance(model, dict)
    vision_model = next(
        (
            item
            for item in running_models
            if str(item.get("name") or item.get("model") or "").removesuffix(":latest")
            == configured_vision_model.removesuffix(":latest")
        ),
        None,
    )
    if not resident_at_snapshot:
        model = next(
            (
                item
                for item in tags.get("models") or []
                if str(item.get("name") or item.get("model") or "").removesuffix(":latest")
                == configured_model.removesuffix(":latest")
            ),
            None,
        )
    if not isinstance(model, dict):
        raise RuntimeError("configured text model metadata is unavailable")
    details = model.get("details") if isinstance(model.get("details"), dict) else {}
    kv_type = _last_log_match(r"K \(([^)]+)\):[^\n]+V \(([^)]+)\):")
    gpu_layers_text = _last_log_match(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU")
    cuda_version = _last_log_match(r"libdirs=ollama,cuda_v[^\s]+\s+driver=([0-9.]+)")
    model_profile = {
        "model_id": str(model.get("name") or model.get("model") or configured_model),
        "model_digest": str(model.get("digest") or ""),
        "parameter_size": str(details.get("parameter_size") or ""),
        "quantization": str(details.get("quantization_level") or ""),
        "runner_name": "Ollama",
        "runner_version": str(version.get("version") or ""),
        "context_length": int(model.get("context_length") or os.getenv("QWEN_CONTEXT_TOKENS", "0")),
        "kv_cache_type": kv_type or "unverified",
        "gpu_layers": int(gpu_layers_text or 0),
        "parallel_slots": int(os.getenv("QWEN_PARALLEL", "1")),
        "prompt_profile": "",
    }
    runtime = {
        "cuda_version": cuda_version or "unverified",
        "running_model_count": len(running_models),
        "model_size_vram_bytes": int(model.get("size_vram") or 0),
        "text_model_resident_at_snapshot": resident_at_snapshot,
        "vision_model_id": configured_vision_model,
        "vision_model_resident_at_snapshot": isinstance(vision_model, dict),
        "vision_model_size_vram_bytes": (
            int(vision_model.get("size_vram") or 0) if isinstance(vision_model, dict) else 0
        ),
    }
    return model_profile, runtime


def _environment_snapshot(expected_driver: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    nvidia = _nvidia_snapshot()
    model_profile, runtime = _ollama_snapshot()
    deployment = {
        **nvidia,
        "cpu_model": _cpu_model(),
        "system_ram_bytes": _system_memory_bytes(),
        "operating_system": platform.platform(),
        "cuda_version": nvidia.get("cuda_version") or runtime["cuda_version"],
    }
    model_profile["runtime_residency"] = runtime
    match = {
        "actual_driver_version": nvidia["driver_version"],
        "expected_driver_version": expected_driver or None,
        "driver_expectation_matches": (
            nvidia["driver_version"] == expected_driver if expected_driver else None
        ),
        "benchmark_bound_to_actual_environment": True,
    }
    return deployment, model_profile, match


def _service_health() -> dict[str, Any]:
    endpoints = {
        "market": "http://127.0.0.1:8010/healthz",
        "line": "http://127.0.0.1:8021/healthz",
        "ollama": "http://127.0.0.1:8020/api/version",
    }
    result: dict[str, Any] = {}
    for name, url in endpoints.items():
        response = requests.get(url, timeout=10)
        ready = response.status_code == 200
        degraded_checks: list[str] = []
        if name == "line" and ready:
            payload = response.json() if response.content else {}
            ready = isinstance(payload, dict) and payload.get("ready") is True
            if isinstance(payload, dict) and not ready:
                degraded_checks = sorted(
                    key
                    for key in (
                        "line_channel_secret_configured",
                        "line_access_token_configured",
                        "market_api_token_configured",
                        "qwen_base_url_configured",
                        "qwen_vision_configured",
                        "signature_verification_enabled",
                        "read_only_asserted",
                        "trading_disabled",
                        "decision_ready_required",
                        "model_recalculation_disabled",
                        "conversation_memory_ready",
                    )
                    if payload.get(key) is not True
                )
        result[name] = {
            "http_status": response.status_code,
            "ready": ready,
            "degraded_checks": degraded_checks,
        }
    if not all(item["ready"] for item in result.values()):
        raise RuntimeError(f"Phase D requires all services healthy: {result}")
    return result


def _probe_health_row() -> dict[str, Any]:
    services: dict[str, Any] = {}
    for name, url in HEALTH_ENDPOINTS.items():
        started = time.monotonic()
        try:
            response = requests.get(url, timeout=0.4)
            ready = response.status_code == 200
            if name == "line_8021" and ready:
                payload = response.json() if response.content else {}
                ready = isinstance(payload, dict) and payload.get("ready") is True
                if isinstance(payload, dict) and not ready:
                    degraded_checks = sorted(
                        key
                        for key in (
                            "line_channel_secret_configured",
                            "line_access_token_configured",
                            "market_api_token_configured",
                            "qwen_base_url_configured",
                            "qwen_vision_configured",
                            "signature_verification_enabled",
                            "read_only_asserted",
                            "trading_disabled",
                            "decision_ready_required",
                            "model_recalculation_disabled",
                            "conversation_memory_ready",
                        )
                        if payload.get(key) is not True
                    )
                else:
                    degraded_checks = []
            else:
                degraded_checks = []
            services[name] = {
                "ready": bool(ready),
                "http_status": int(response.status_code),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "degraded_checks": degraded_checks,
            }
        except (requests.RequestException, ValueError) as exc:
            services[name] = {
                "ready": False,
                "http_status": None,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error_class": type(exc).__name__,
            }
    return {"observed_at": _now(), "services": services}


def _health_sampler(stop_event: threading.Event, path: Path) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        while not stop_event.is_set():
            handle.write(json.dumps(_probe_health_row(), ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            stop_event.wait(0.5)


def _health_evidence_summary(path: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    invalid_rows = 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                invalid_rows += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                invalid_rows += 1
    not_ready = {
        name: sum(
            not bool((row.get("services") or {}).get(name, {}).get("ready"))
            for row in rows
        )
        for name in HEALTH_ENDPOINTS
    }
    return {
        "evidence_file": path.name,
        "sample_count": len(rows),
        "invalid_row_count": invalid_rows,
        "first_observed_at": rows[0].get("observed_at") if rows else None,
        "last_observed_at": rows[-1].get("observed_at") if rows else None,
        "not_ready_by_service": not_ready,
        "all_services_ready": bool(rows) and not any(not_ready.values()),
    }


def _line_reply_telemetry_summary(day: date) -> dict[str, Any]:
    configured = str(os.getenv("LINE_REPLY_TELEMETRY_PATH") or "").strip()
    path = Path(configured).expanduser() if configured else Path(
        "logs/line_model_shadow/line_reply_telemetry.jsonl"
    )
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    rows: list[dict[str, Any]] = []
    invalid_rows = 0
    if path.is_file():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    invalid_rows += 1
                    continue
                if not isinstance(row, dict):
                    invalid_rows += 1
                    continue
                if str(row.get("captured_at") or "").startswith(day.isoformat()):
                    rows.append(row)
    sent = [row for row in rows if row.get("reply_status") == "sent"]
    failed = [row for row in rows if row.get("reply_status") == "failed"]
    shadow_active = [row for row in sent if row.get("shadow_active_at_handler_start") is True]
    canary_selected = [row for row in sent if row.get("candidate_selected") is True]
    canary_delivered = [row for row in sent if row.get("candidate_delivered") is True]

    def numeric(items: list[dict[str, Any]], field: str) -> list[float]:
        return [
            float(item[field])
            for item in items
            if isinstance(item.get(field), (int, float))
        ]

    return {
        "contract_version": "line-reply-telemetry-daily-summary-v2",
        "local_date": day.isoformat(),
        "source_path": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
        "raw_message_or_identity_persisted": False,
        "sample_count": len(rows),
        "sent_count": len(sent),
        "failed_count": len(failed),
        "invalid_row_count": invalid_rows,
        "shadow_active_at_handler_start_count": len(shadow_active),
        "canary_selected_count": len(canary_selected),
        "canary_delivered_count": len(canary_delivered),
        "webhook_ingress_to_reply_p95_ms": _p95(numeric(sent, "webhook_ingress_to_reply_ms")),
        "line_send_p95_ms": _p95(numeric(sent, "line_send_ms")),
        "shadow_active_webhook_ingress_to_reply_p95_ms": _p95(
            numeric(shadow_active, "webhook_ingress_to_reply_ms")
        ),
        "canary_selected_webhook_ingress_to_reply_p95_ms": _p95(
            numeric(canary_selected, "webhook_ingress_to_reply_ms")
        ),
        "canary_delivered_webhook_ingress_to_reply_p95_ms": _p95(
            numeric(canary_delivered, "webhook_ingress_to_reply_ms")
        ),
        "gate_status": "observed" if sent else "pending_real_line_delivery_telemetry",
    }


def _post_sample(token: str, scenario: str, code: str) -> dict[str, Any]:
    started = time.monotonic()
    response = requests.post(
        f"{BASE_URL}/shadow-sample",
        headers={"Authorization": f"Bearer {token}"},
        json={"scenario": scenario, "code": code},
        timeout=180,
    )
    try:
        body: Any = response.json()
    except ValueError:
        body = {"non_json_sha256": hashlib.sha256(response.content).hexdigest()}
    return {
        "attempt_id": uuid.uuid4().hex,
        "scenario": scenario,
        "http_status": response.status_code,
        "http_round_trip_ms": int((time.monotonic() - started) * 1000),
        "finished_at": _now(),
        "body": body,
    }


def _control_research_cache(token: str, *, mode: str, code: str) -> dict[str, Any]:
    response = requests.post(
        f"{BASE_URL}/research-cache-control",
        headers={"Authorization": f"Bearer {token}"},
        json={"scenario": RESEARCH_CACHE_SCENARIO, "code": code, "mode": mode},
        timeout=30,
    )
    try:
        body: Any = response.json()
    except ValueError:
        body = {"non_json_sha256": hashlib.sha256(response.content).hexdigest()}
    if response.status_code != 200:
        raise RuntimeError(f"research cache {mode} control failed with HTTP {response.status_code}")
    if not isinstance(body, dict):
        raise RuntimeError("research cache control returned a non-object response")
    if mode == "prime" and body.get("primed") is not True:
        raise RuntimeError("research cache prime did not execute a bounded live retrieval")
    return body


def _news_cache_assertion(
    attempts: list[dict[str, Any]],
    requested_state: str,
) -> dict[str, Any]:
    states: list[str] = []
    retrieval_modes: list[str] = []
    for attempt in attempts:
        body = attempt.get("body") if isinstance(attempt.get("body"), dict) else {}
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        summary = result.get("research_summary") if isinstance(result.get("research_summary"), dict) else {}
        if int(summary.get("query_count") or 0) < 1:
            continue
        states.append(str(summary.get("cache_state") or "unknown"))
        retrieval_modes.append(str(summary.get("retrieval_mode") or "unknown"))
    if requested_state == "cache_only":
        matches: bool | None = None
        reason = "observed_only_no_forced_cache_state"
    elif requested_state == "hit":
        matches = bool(states) and set(states) == {"hit"}
        reason = "all_research_samples_hit" if matches else "not_all_research_samples_hit"
    else:
        matches = "miss" in states
        reason = "at_least_one_bounded_live_miss" if matches else "no_bounded_live_miss_observed"
    return {
        "requested": requested_state,
        "observed_cache_states": states,
        "observed_retrieval_modes": retrieval_modes,
        "research_sample_count": len(states),
        "matches": matches,
        "reason": reason,
    }


def _canonical_state_assertion(
    attempts: list[dict[str, Any]],
    requested_state: str,
    *,
    field: str,
) -> dict[str, Any]:
    states: list[str] = []
    for attempt in attempts:
        body = attempt.get("body") if isinstance(attempt.get("body"), dict) else {}
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        state = str(result.get(field) or "")
        if state in {"hit", "miss"}:
            states.append(state)
    if requested_state == "observed":
        matches: bool | None = None
        reason = "observed_only_no_required_state"
    else:
        matches = bool(states) and set(states) == {requested_state}
        reason = (
            f"all_canonical_samples_{requested_state}"
            if matches
            else f"not_all_canonical_samples_{requested_state}"
        )
    return {
        "requested": requested_state,
        "observed_states": states,
        "sample_count": len(states),
        "matches": matches,
        "reason": reason,
        "source_field": field,
        "canonical_path": True,
    }


def _measurement_summary(attempts: list[dict[str, Any]], reply_budget_ms: int, work_stop_ms: int) -> dict[str, Any]:
    completed: list[dict[str, Any]] = []
    validator = Counter()
    errors = Counter()
    for attempt in attempts:
        body = attempt.get("body") if isinstance(attempt.get("body"), dict) else {}
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        if attempt.get("http_status") == 200 and result.get("candidate_model_called"):
            completed.append(attempt)
        validator[str(result.get("validator_result") or "http_error")] += 1
        if result.get("error_classification"):
            errors[str(result["error_classification"])] += 1

    def values(field: str) -> list[float]:
        found: list[float] = []
        for attempt in completed:
            body = attempt["body"]
            result = body["result"]
            if field == "queue_wait":
                value = body.get("queue_wait_ms")
            elif field == "end_to_end":
                value = body.get("benchmark_end_to_end_ms")
            elif field == "http_round_trip":
                value = attempt.get("http_round_trip_ms")
            else:
                value = (result.get("stage_timings_ms") or {}).get(field)
            if isinstance(value, (int, float)):
                found.append(float(value))
        return found

    stage_names = (
        "queue_wait",
        "retrieval",
        "projection",
        "classification",
        "packet_build",
        "generation",
        "validation",
        "render",
        "end_to_end",
        "http_round_trip",
    )
    p95 = {f"{name}_p95_ms": _p95(values(name)) for name in stage_names}
    predicted_components = (
        "queue_wait_p95_ms",
        "retrieval_p95_ms",
        "projection_p95_ms",
        "classification_p95_ms",
        "packet_build_p95_ms",
        "generation_p95_ms",
        "validation_p95_ms",
        "render_p95_ms",
    )
    predicted = round(sum(float(p95.get(key) or 0) for key in predicted_components), 3)
    required_stage_keys = (
        "classification_p95_ms",
        "retrieval_p95_ms",
        "generation_p95_ms",
        "validation_p95_ms",
        "render_p95_ms",
    )
    missing_stage_measurements = [
        key.removesuffix("_p95_ms") for key in required_stage_keys if p95.get(key) is None
    ]
    stage_measurement_complete = bool(completed) and not missing_stage_measurements
    ungrounded = sum(
        int((attempt["body"]["result"]).get("ungrounded_claim_count") or 0)
        for attempt in completed
    )
    referee_overrides = sum(
        int((attempt["body"]["result"]).get("referee_override_count") or 0)
        for attempt in completed
    )
    context_overflow = sum(
        str((attempt["body"]["result"]).get("error_classification") or "") == "context_http_400"
        for attempt in completed
    )
    return {
        "completed_samples": len(completed),
        "failed_samples": len(attempts) - len(completed),
        "validator_counts": dict(sorted(validator.items())),
        "error_classification_counts": dict(sorted(errors.items())),
        **p95,
        "predicted_work_p95_ms": predicted,
        "required_stage_measurements_complete": stage_measurement_complete,
        "missing_stage_measurements": missing_stage_measurements,
        "reply_budget_ms": reply_budget_ms,
        "work_stop_budget_ms": work_stop_ms,
        "candidate_work_stop_gate_pass": (
            stage_measurement_complete and predicted < work_stop_ms
        ),
        "actual_webhook_ingress_to_line_reply_p95_ms": None,
        "actual_line_reply_gate_status": "pending_real_line_delivery_telemetry",
        "ungrounded_numeric_or_date_claims": ungrounded,
        "referee_overrides": referee_overrides,
        "context_overflow_failures": context_overflow,
    }


def _aggregate(root: Path) -> dict[str, Any]:
    manifests = []
    for path in sorted(root.glob("*/daily_manifest.json")):
        try:
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
    trading_days = sorted(
        {
            str(item.get("trading_day", {}).get("local_date"))
            for item in manifests
            if item.get("trading_day", {}).get("qualifies_as_trading_day")
        }
    )
    counts = Counter()
    line_reply_samples = 0
    shadow_active_line_reply_samples = 0
    line_reply_daily_p95: list[float] = []
    for manifest in manifests:
        if not manifest.get("trading_day", {}).get("qualifies_as_trading_day"):
            continue
        for profile, count in dict(manifest.get("completed_samples_by_profile") or {}).items():
            counts[str(profile)] += int(count)
        telemetry = manifest.get("line_reply_telemetry") or {}
        line_reply_samples += int(telemetry.get("sent_count") or 0)
        shadow_active_line_reply_samples += int(
            telemetry.get("shadow_active_at_handler_start_count") or 0
        )
        if isinstance(telemetry.get("webhook_ingress_to_reply_p95_ms"), (int, float)):
            line_reply_daily_p95.append(float(telemetry["webhook_ingress_to_reply_p95_ms"]))
    return {
        "contract_version": "phase-d-aggregate-v1",
        "reviewed_at": _now(),
        "trading_days": trading_days,
        "trading_day_count": len(trading_days),
        "completed_samples_by_profile": dict(sorted(counts.items())),
        "actual_line_reply_sent_samples": line_reply_samples,
        "actual_line_reply_shadow_active_samples": shadow_active_line_reply_samples,
        "maximum_daily_webhook_ingress_to_reply_p95_ms": (
            max(line_reply_daily_p95) if line_reply_daily_p95 else None
        ),
        "actual_line_reply_gate_status": (
            "observed_with_shadow_contention"
            if shadow_active_line_reply_samples > 0
            else ("observed_without_shadow_contention" if line_reply_samples > 0 else "pending")
        ),
        "five_trading_days_pass": len(trading_days) >= 5,
        "profile_100_samples_pass": all(counts.get(profile, 0) >= 100 for profile in PROFILE_SCENARIOS),
        "phase_d_sample_gate_pass": (
            len(trading_days) >= 5
            and all(counts.get(profile, 0) >= 100 for profile in PROFILE_SCENARIOS)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--run-label", default="daily")
    parser.add_argument("--profiles", choices=("focused", "comprehensive", "all"), default="all")
    parser.add_argument("--samples-per-case", type=int, default=5)
    parser.add_argument("--concurrency", type=int, nargs="+", default=list(CONCURRENCY_LEVELS))
    parser.add_argument("--code", default="2330")
    parser.add_argument("--model-state", choices=("warm", "cold"), default="warm")
    parser.add_argument("--news-cache", choices=("hit", "miss", "cache_only"), default="cache_only")
    parser.add_argument(
        "--canonical-artifact-cache",
        choices=("observed", "hit", "miss"),
        default="observed",
    )
    parser.add_argument(
        "--canonical-news",
        choices=("observed", "hit", "miss"),
        default="observed",
    )
    parser.add_argument("--contention", action="append", default=[])
    parser.add_argument("--expected-driver", default="")
    parser.add_argument("--output-root", default=str(PHASE_D_ROOT.relative_to(PROJECT_ROOT)))
    parser.add_argument("--aggregate-only", action="store_true")
    args = parser.parse_args()

    selected_profiles = tuple(PROFILE_SCENARIOS) if args.profiles == "all" else (args.profiles,)
    concurrency_levels = tuple(dict.fromkeys(int(item) for item in args.concurrency))
    if any(item not in CONCURRENCY_LEVELS for item in concurrency_levels):
        raise ValueError("concurrency must be selected from 1, 2, 4, 8")
    if args.model_state != "warm":
        raise RuntimeError("cold runs require a separately recorded maintenance operation")
    if args.contention:
        raise RuntimeError(
            "--contention cannot be used as a label; run the real Phase D contention probes"
        )
    if args.news_cache != "cache_only" and selected_profiles != ("comprehensive",):
        raise RuntimeError(
            "forced news-cache hit/miss evidence requires --profiles comprehensive"
        )

    root = Path(args.output_root)
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root.mkdir(parents=True, exist_ok=True)
    if args.aggregate_only:
        aggregate = _aggregate(root)
        (root / "aggregate_status.json").write_text(
            json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(aggregate, ensure_ascii=False, indent=2))
        return 0

    day = date.fromisoformat(args.date)
    run_label = re.sub(r"[^a-z0-9_-]+", "-", str(args.run_label).strip().lower()).strip("-")
    if not run_label:
        raise ValueError("run label must contain at least one safe character")
    day_dir = root / (day.isoformat() if run_label == "daily" else f"{day.isoformat()}-{run_label}")
    if day_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing Phase D day: {day_dir}")
    day_dir.mkdir(parents=True)
    raw_path = day_dir / "attempts.jsonl"
    health_path = day_dir / "health.jsonl"
    manifest_path = day_dir / "daily_manifest.json"
    values = dotenv_values(PRIVATE_ENV)
    token = str(values.get("BOT_MARKET_DATA_TOKEN") or "")
    if len(token) < 32:
        raise RuntimeError("persistent BOT_MARKET_DATA_TOKEN is unavailable")
    os.environ.update({key: str(value) for key, value in values.items() if value is not None})
    health_before = _service_health()
    deployment, base_model_profile, environment_match = _environment_snapshot(args.expected_driver)
    reply_budget_ms = int(values.get("LINE_TOTAL_REPLY_BUDGET_SECONDS") or 45) * 1000
    reply_reserve_ms = (int(values.get("LINE_REPLY_TIMEOUT_SECONDS") or 12) + 2) * 1000
    work_stop_ms = max(0, reply_budget_ms - reply_reserve_ms)
    completed_by_profile = Counter()
    evidence_files: list[str] = []
    write_lock = threading.Lock()
    raw_handle = raw_path.open("x", encoding="utf-8", newline="\n")
    health_stop = threading.Event()
    health_thread = threading.Thread(
        target=_health_sampler,
        args=(health_stop, health_path),
        name="phase-d-health-sampler",
        daemon=True,
    )
    health_thread.start()
    try:
        for profile in selected_profiles:
            scenarios = PROFILE_SCENARIOS[profile]
            for concurrency in concurrency_levels:
                cache_precondition: dict[str, Any] | None = None
                if args.news_cache == "miss":
                    cache_precondition = _control_research_cache(
                        token,
                        mode="clear",
                        code=args.code,
                    )
                elif args.news_cache == "hit":
                    cache_precondition = _control_research_cache(
                        token,
                        mode="prime",
                        code=args.code,
                    )
                count = max(int(args.samples_per_case), concurrency)
                case_attempts: list[dict[str, Any]] = []
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = {
                        executor.submit(_post_sample, token, scenarios[index % len(scenarios)], args.code): index
                        for index in range(count)
                    }
                    for future in as_completed(futures):
                        attempt = future.result()
                        attempt.update(
                            {
                                "profile_family": profile,
                                "concurrent_arrivals": concurrency,
                                "model_state": args.model_state,
                                "news_cache": args.news_cache,
                                "canonical_artifact_cache": args.canonical_artifact_cache,
                                "canonical_news": args.canonical_news,
                                "contention": list(args.contention),
                            }
                        )
                        case_attempts.append(attempt)
                        with write_lock:
                            raw_handle.write(
                                json.dumps(attempt, ensure_ascii=False, separators=(",", ":")) + "\n"
                            )
                            raw_handle.flush()
                        print(
                            json.dumps(
                                {
                                    "profile": profile,
                                    "concurrency": concurrency,
                                    "http": attempt["http_status"],
                                    "scenario": attempt["scenario"],
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                measurements = _measurement_summary(case_attempts, reply_budget_ms, work_stop_ms)
                news_cache_assertion = _news_cache_assertion(case_attempts, args.news_cache)
                canonical_artifact_cache_assertion = _canonical_state_assertion(
                    case_attempts,
                    args.canonical_artifact_cache,
                    field="canonical_artifact_cache_state",
                )
                canonical_news_assertion = _canonical_state_assertion(
                    case_attempts,
                    args.canonical_news,
                    field="canonical_news_state",
                )
                completed_by_profile[profile] += measurements["completed_samples"]
                observed_profiles = {
                    str((attempt.get("body", {}).get("result") or {}).get("profile") or "")
                    for attempt in case_attempts
                    if isinstance(attempt.get("body"), dict)
                }
                expected_prompt_profile = f"{profile}-16k-v1"
                case_profile = {
                    **base_model_profile,
                    "prompt_profile": expected_prompt_profile,
                }
                case_artifact = {
                    "contract_version": "release-benchmark-evidence-v1",
                    "benchmark_id": uuid.uuid4().hex,
                    "captured_at": _now(),
                    "trading_day": _trading_day_status(day),
                    "deployment_target": deployment,
                    "environment_match": environment_match,
                    "model_profile": case_profile,
                    "source_hashes": {
                        relative: _sha256(PROJECT_ROOT / relative) for relative in SOURCE_BINDINGS
                    },
                    "load_case": {
                        "concurrent_arrivals": concurrency,
                        "model_state": args.model_state,
                        "news_cache": args.news_cache,
                        "canonical_artifact_cache": args.canonical_artifact_cache,
                        "canonical_news": args.canonical_news,
                        "contention": list(args.contention),
                        "scenario_count": len(case_attempts),
                    },
                    "news_cache_precondition": cache_precondition,
                    "news_cache_assertion": news_cache_assertion,
                    "canonical_artifact_cache_assertion": (
                        canonical_artifact_cache_assertion
                    ),
                    "canonical_news_assertion": canonical_news_assertion,
                    "measurements": measurements,
                    "profile_assertion": {
                        "expected": expected_prompt_profile,
                        "observed": sorted(observed_profiles),
                        "matches": observed_profiles == {expected_prompt_profile},
                    },
                    "raw_attempts": raw_path.name,
                    "health_evidence": health_path.name,
                    "valid_for_release_p95": False,
                    "release_blockers": [
                        "daily artifact alone does not satisfy five trading days",
                        "daily artifact alone does not satisfy 100 completed samples per profile",
                        "actual LINE reply delivery p95 is not yet available",
                    ] + (
                        ["required canonical stage measurements are incomplete"]
                        if not measurements["required_stage_measurements_complete"]
                        else []
                    ) + (
                        ["forced news-cache state was not observed"]
                        if news_cache_assertion["matches"] is False
                        else []
                    ) + (
                        ["required canonical artifact-cache state was not observed"]
                        if canonical_artifact_cache_assertion["matches"] is False
                        else []
                    ) + (
                        ["required canonical news-evidence state was not observed"]
                        if canonical_news_assertion["matches"] is False
                        else []
                    ),
                }
                case_name = f"{profile}-c{concurrency}-{args.model_state}.json"
                (day_dir / case_name).write_text(
                    json.dumps(case_artifact, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                evidence_files.append(case_name)
    finally:
        raw_handle.close()
        health_stop.set()
        health_thread.join(timeout=3)
    health_after = _service_health()
    health_evidence = _health_evidence_summary(health_path)
    line_reply_telemetry = _line_reply_telemetry_summary(day)
    manifest = {
        "contract_version": "phase-d-daily-manifest-v1",
        "captured_at": _now(),
        "run_label": run_label,
        "trading_day": _trading_day_status(day),
        "deployment_target": deployment,
        "environment_match": environment_match,
        "completed_samples_by_profile": dict(sorted(completed_by_profile.items())),
        "load_matrix": {
            "profiles": list(selected_profiles),
            "concurrency_levels": list(concurrency_levels),
            "model_state": args.model_state,
            "news_cache": args.news_cache,
            "canonical_artifact_cache": args.canonical_artifact_cache,
            "canonical_news": args.canonical_news,
            "contention": list(args.contention),
        },
        "health_before": health_before,
        "health_after": health_after,
        "health_evidence": health_evidence,
        "line_reply_telemetry": line_reply_telemetry,
        "evidence_files": evidence_files,
        "raw_attempts": raw_path.name,
        "valid_for_five_day_gate": _trading_day_status(day)["qualifies_as_trading_day"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    aggregate = _aggregate(root)
    (root / "aggregate_status.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"daily_manifest": str(manifest_path), "aggregate": aggregate}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
