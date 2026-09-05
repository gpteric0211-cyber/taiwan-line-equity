from __future__ import annotations

"""Measure fixed production stable-reply admission and early DB fallback behavior."""

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import run_line_model_phase_d as phase_d  # noqa: E402


PRIVATE_ENV = PROJECT_ROOT / ".env.line_bot"
BASE_URL = "http://127.0.0.1:8021/internal/line-model-benchmark"
SCENARIOS = {
    "focused": "focused_technical",
    "comprehensive": "fundamental_chip_technical_news",
}
SOURCE_BINDINGS = (
    "scripts/run_line_stable_admission_load.py",
    "review_src/adapter/qwen_local.py",
    "review_src/services/line_bot_service.py",
    "review_src/services/model_admission_service.py",
    "review_src/services/line_model_benchmark_service.py",
    "review_src/api/line_model_benchmark.py",
)


def _post(token: str, scenario: str) -> dict[str, Any]:
    started = time.monotonic()
    response = requests.post(
        f"{BASE_URL}/stable-reply-sample",
        headers={"Authorization": f"Bearer {token}"},
        json={"scenario": scenario, "code": "2330"},
        timeout=70,
    )
    try:
        body: Any = response.json()
    except ValueError:
        body = {"non_json_sha256": hashlib.sha256(response.content).hexdigest()}
    return {
        "attempt_id": uuid.uuid4().hex,
        "http_status": response.status_code,
        "http_round_trip_ms": int((time.monotonic() - started) * 1000),
        "body": body,
        "finished_at": phase_d._now(),
    }


def _summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    paths = Counter(
        str(attempt.get("body", {}).get("answer_path") or "http_error")
        for attempt in attempts
    )
    completed = [attempt for attempt in attempts if attempt.get("http_status") == 200]
    end_to_end = [
        float(attempt["body"]["end_to_end_ms"])
        for attempt in completed
        if isinstance(attempt.get("body", {}).get("end_to_end_ms"), (int, float))
    ]
    round_trip = [float(attempt["http_round_trip_ms"]) for attempt in attempts]
    within_budget = sum(
        attempt.get("body", {}).get("completed_within_internal_reply_budget") is True
        for attempt in completed
    )
    disclaimer = sum(
        attempt.get("body", {}).get("reply_has_required_disclaimer") is True
        for attempt in completed
    )
    early_fallback = sum(
        str(attempt.get("body", {}).get("answer_path") or "").startswith(
            "admission_fallback:predicted_deadline_admission_rejected"
        )
        for attempt in completed
    )
    return {
        "attempt_count": len(attempts),
        "http_200_count": len(completed),
        "answer_path_counts": dict(sorted(paths.items())),
        "model_answer_count": paths.get("model", 0),
        "predicted_deadline_early_fallback_count": early_fallback,
        "within_internal_reply_budget_count": within_budget,
        "required_disclaimer_count": disclaimer,
        "end_to_end_p95_ms": phase_d._p95(end_to_end),
        "http_round_trip_p95_ms": phase_d._p95(round_trip),
        "all_http_200": len(completed) == len(attempts),
        "all_within_internal_reply_budget": within_budget == len(attempts),
        "all_have_required_disclaimer": disclaimer == len(attempts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profiles", choices=("focused", "comprehensive", "all"), default="all")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--samples-per-case", type=int, default=1)
    parser.add_argument("--expected-driver", default="")
    args = parser.parse_args()

    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    raw_output = output.with_suffix(".jsonl")
    health_output = output.with_name(f"{output.stem}_health.jsonl")
    for target in (output, raw_output, health_output):
        if target.exists():
            raise FileExistsError(f"refusing to overwrite stable admission evidence: {target}")
    output.parent.mkdir(parents=True, exist_ok=True)

    values = dotenv_values(PRIVATE_ENV)
    token = str(values.get("BOT_MARKET_DATA_TOKEN") or "")
    if len(token) < 32:
        raise RuntimeError("persistent BOT_MARKET_DATA_TOKEN is unavailable")
    os.environ.update({key: str(value) for key, value in values.items() if value is not None})
    concurrency_levels = tuple(dict.fromkeys(int(value) for value in args.concurrency))
    if any(value not in phase_d.CONCURRENCY_LEVELS for value in concurrency_levels):
        raise ValueError("concurrency must be selected from 1, 2, 4, 8")
    selected_profiles = tuple(SCENARIOS) if args.profiles == "all" else (args.profiles,)

    health_before = phase_d._service_health()
    warmup_response = requests.post(
        f"{BASE_URL}/interactive-warmup",
        headers={"Authorization": f"Bearer {token}"},
        json={"acknowledge_possible_interactive_delay": True},
        timeout=140,
    )
    if warmup_response.status_code != 200:
        raise RuntimeError(f"interactive warmup failed with HTTP {warmup_response.status_code}")
    deployment, model_profile, environment_match = phase_d._environment_snapshot(args.expected_driver)

    cases: list[dict[str, Any]] = []
    health_stop = threading.Event()
    health_thread = threading.Thread(
        target=phase_d._health_sampler,
        args=(health_stop, health_output),
        name="stable-load-health-sampler",
        daemon=True,
    )
    health_thread.start()
    try:
        with raw_output.open("x", encoding="utf-8", newline="\n") as raw_handle:
            for profile in selected_profiles:
                scenario = SCENARIOS[profile]
                for concurrency in concurrency_levels:
                    count = max(1, int(args.samples_per_case), concurrency)
                    attempts: list[dict[str, Any]] = []
                    with ThreadPoolExecutor(max_workers=concurrency) as executor:
                        futures = [executor.submit(_post, token, scenario) for _ in range(count)]
                        for future in as_completed(futures):
                            attempt = future.result()
                            attempt.update(
                                {
                                    "profile": profile,
                                    "scenario": scenario,
                                    "concurrent_arrivals": concurrency,
                                }
                            )
                            attempts.append(attempt)
                            raw_handle.write(
                                json.dumps(attempt, ensure_ascii=False, separators=(",", ":")) + "\n"
                            )
                            raw_handle.flush()
                    case = {
                        "profile": profile,
                        "scenario": scenario,
                        "concurrent_arrivals": concurrency,
                        "summary": _summary(attempts),
                    }
                    cases.append(case)
                    print(json.dumps(case, ensure_ascii=False), flush=True)
    finally:
        health_stop.set()
        health_thread.join(timeout=3)

    high_concurrency = [case for case in cases if case["concurrent_arrivals"] in {4, 8}]
    high_concurrency_early_fallback_observed = all(
        case["summary"]["predicted_deadline_early_fallback_count"] > 0
        for case in high_concurrency
    )
    all_bounded = all(case["summary"]["all_within_internal_reply_budget"] for case in cases)
    health_evidence = phase_d._health_evidence_summary(health_output)
    artifact = {
        "contract_version": "stable-reply-admission-load-diagnostic-v1",
        "captured_at": phase_d._now(),
        "trading_day": phase_d._trading_day_status(phase_d.date.today()),
        "deployment_target": deployment,
        "environment_match": environment_match,
        "model_profile": model_profile,
        "source_hashes": {
            relative: phase_d._sha256(PROJECT_ROOT / relative) for relative in SOURCE_BINDINGS
        },
        "load_cases": cases,
        "health_before": health_before,
        "health_after": phase_d._service_health(),
        "health_evidence": health_evidence,
        "high_concurrency_early_fallback_observed": high_concurrency_early_fallback_observed,
        "all_stable_replies_within_internal_budget": all_bounded,
        "actual_line_network_send_included": False,
        "candidate_reply_replacement_included": False,
        "valid_for_release_load_matrix": False,
        "limitations": [
            "fixed authenticated synthetic arrivals, not real LINE webhook traffic",
            "does not include LINE reply network send",
            "non-trading-day diagnostic does not advance the five-day gate",
        ],
        "raw_attempts": raw_output.name,
        "health_samples": health_output.name,
    }
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "all_stable_replies_within_internal_budget": all_bounded,
                "high_concurrency_early_fallback_observed": high_concurrency_early_fallback_observed,
            },
            ensure_ascii=False,
        )
    )
    return 0 if (
        all_bounded
        and high_concurrency_early_fallback_observed
        and health_evidence["all_services_ready"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
