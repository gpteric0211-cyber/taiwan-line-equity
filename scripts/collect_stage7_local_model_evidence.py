from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SRC = PROJECT_ROOT / "review_src"
if str(REVIEW_SRC) not in sys.path:
    sys.path.insert(0, str(REVIEW_SRC))

from core.line_bot_config import env_text, load_line_bot_env  # noqa: E402


EXPECTED_GPU = "NVIDIA GeForce RTX 5090"
EXPECTED_DRIVER = "595.71"
EXPECTED_CUDA = "13.3"
OLLAMA_BASE_URL = "http://127.0.0.1:8020"
BENCHMARK_URL = "http://127.0.0.1:8021/internal/line-model-benchmark/shadow-sample"


def _nearest_rank(values: list[int], proportion: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * proportion) - 1)]


def _runtime_snapshot() -> dict[str, Any]:
    version_response = requests.get(f"{OLLAMA_BASE_URL}/api/version", timeout=5)
    version_response.raise_for_status()
    process_response = requests.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=10)
    process_response.raise_for_status()
    version = version_response.json()
    process = process_response.json()
    configured_model = env_text("QWEN_MODEL_ID", "taiwan-stock-qwen")
    models = list(process.get("models") or []) if isinstance(process, dict) else []
    model = next(
        (
            item
            for item in models
            if str(item.get("name") or item.get("model") or "").removesuffix(":latest")
            == configured_model.removesuffix(":latest")
        ),
        None,
    )
    if not isinstance(model, dict):
        raise RuntimeError("configured text model is not resident")
    details = model.get("details") if isinstance(model.get("details"), dict) else {}
    nvidia = subprocess.run(
        ["nvidia-smi"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    header = re.search(r"NVIDIA-SMI\s+([^\s]+).*?CUDA(?: UMD)? Version:\s*([^\s]+)", nvidia)
    gpu_line = re.search(r"\|\s+0\s+(.+?)\s+(?:WDDM|TCC)\s+\|", nvidia)
    memory = re.search(r"(\d+)MiB\s*/\s*(\d+)MiB", nvidia)
    size = int(model.get("size") or 0)
    size_vram = int(model.get("size_vram") or 0)
    return {
        "gpu": gpu_line.group(1).strip() if gpu_line else "unverified",
        "gpu_memory_used_mib": int(memory.group(1)) if memory else None,
        "gpu_memory_total_mib": int(memory.group(2)) if memory else None,
        "driver_version": header.group(1) if header else "unverified",
        "cuda_version": header.group(2) if header else "unverified",
        "runner": "Ollama",
        "runner_version": str(version.get("version") or ""),
        "model_id": str(model.get("name") or model.get("model") or configured_model),
        "model_digest": str(model.get("digest") or ""),
        "parameter_size": str(details.get("parameter_size") or ""),
        "quantization": str(details.get("quantization_level") or ""),
        "context_length": int(model.get("context_length") or 0),
        "model_size_bytes": size,
        "model_size_vram_bytes": size_vram,
        "cpu_offload_bytes": max(0, size - size_vram),
    }


def _sample(token: str, scenario: str) -> dict[str, Any]:
    started = time.monotonic()
    response = requests.post(
        BENCHMARK_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"scenario": scenario, "code": "2330"},
        timeout=180,
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if response.status_code != 200:
        return {
            "http_status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "error": "benchmark_http_error",
        }
    payload = response.json()
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    output = str(result.get("model_output") or "")
    return {
        "http_status": response.status_code,
        "request_id": str(payload.get("request_id") or ""),
        "scenario": scenario,
        "profile": str(result.get("profile") or ""),
        "elapsed_ms": elapsed_ms,
        "queue_wait_ms": int(payload.get("queue_wait_ms") or 0),
        "execution_ms": int(payload.get("execution_ms") or 0),
        "benchmark_end_to_end_ms": int(payload.get("benchmark_end_to_end_ms") or 0),
        "packet_token_count": result.get("packet_token_count"),
        "prompt_token_count": result.get("prompt_token_count"),
        "completion_token_count": result.get("completion_token_count"),
        "finish_reason": result.get("finish_reason"),
        "validator_result": result.get("validator_result"),
        "validator_reason_codes": list(result.get("validator_reason_codes") or []),
        "ungrounded_claim_count": int(result.get("ungrounded_claim_count") or 0),
        "referee_override_count": int(result.get("referee_override_count") or 0),
        "candidate_can_replace_reply": bool(result.get("candidate_can_replace_reply")),
        "model_output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "model_output_characters": len(output),
        "replacement_character_count": output.count("\ufffd"),
        "stage_timings_ms": dict(result.get("stage_timings_ms") or {}),
    }


def _level(token: str, concurrency: int, scenario: str) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(_sample, token, scenario) for _ in range(concurrency)]
        samples = [future.result() for future in as_completed(futures)]
    latencies = [int(sample["benchmark_end_to_end_ms"]) for sample in samples if sample.get("http_status") == 200]
    queue_waits = [int(sample["queue_wait_ms"]) for sample in samples if sample.get("http_status") == 200]
    return {
        "concurrency": concurrency,
        "submitted": concurrency,
        "completed": sum(sample.get("http_status") == 200 for sample in samples),
        "validator_passed": sum(sample.get("validator_result") == "pass" for sample in samples),
        "validator_rejected": sum(sample.get("validator_result") == "reject" for sample in samples),
        "end_to_end_p95_ms": _nearest_rank(latencies, 0.95),
        "queue_wait_p95_ms": _nearest_rank(queue_waits, 0.95),
        "samples": sorted(samples, key=lambda sample: str(sample.get("request_id") or "")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect sanitized Stage 7 local-model evidence.")
    parser.add_argument("--concurrency", default="1,2,4,8")
    parser.add_argument("--scenario", default="focused_technical")
    args = parser.parse_args()
    load_line_bot_env()
    token = env_text("BOT_MARKET_DATA_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("BOT_MARKET_DATA_TOKEN is unavailable")
    levels = [int(value) for value in str(args.concurrency).split(",") if value.strip()]
    if not levels or any(value not in {1, 2, 4, 8} for value in levels):
        raise SystemExit("concurrency must be a subset of 1,2,4,8")
    runtime = _runtime_snapshot()
    matrix = [_level(token, level, str(args.scenario)) for level in levels]
    samples = [sample for level in matrix for sample in level["samples"]]
    blockers: list[str] = []
    if runtime["gpu"] != EXPECTED_GPU:
        blockers.append("gpu_identity_mismatch")
    if runtime["driver_version"] != EXPECTED_DRIVER:
        blockers.append("driver_version_mismatch")
    if runtime["cuda_version"] != EXPECTED_CUDA:
        blockers.append("cuda_version_mismatch")
    if runtime["context_length"] != 16_384:
        blockers.append("stable_16k_context_not_verified")
    if runtime["cpu_offload_bytes"] != 0:
        blockers.append("cpu_offload_detected")
    if any(sample.get("validator_result") != "pass" for sample in samples):
        blockers.append("actual_candidate_validator_rejection")
    if len(samples) < 100:
        blockers.append("phase_d_real_execution_minimum_not_met")
    result = {
        "evidence_contract": "Stage7LocalModelEvidenceV1",
        "source": "actual_local_hardware_execution",
        "synthetic_latency_rows": 0,
        "candidate_reply_sent_to_line": False,
        "expected": {
            "gpu": EXPECTED_GPU,
            "driver_version": EXPECTED_DRIVER,
            "cuda_version": EXPECTED_CUDA,
        },
        "runtime": runtime,
        "profile_decision": "focused/comprehensive 16K only; 32K remains unqualified",
        "matrix": matrix,
        "summary": {
            "actual_executions": len(samples),
            "validator_passed": sum(sample.get("validator_result") == "pass" for sample in samples),
            "validator_rejected": sum(sample.get("validator_result") == "reject" for sample in samples),
            "replacement_character_outputs": sum(int(sample.get("replacement_character_count") or 0) > 0 for sample in samples),
        },
        "release_blockers": list(dict.fromkeys(blockers)),
        "release_ready": not blockers,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
