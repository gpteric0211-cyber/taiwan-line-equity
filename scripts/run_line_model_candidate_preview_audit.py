from __future__ import annotations

"""Collect three final-path candidate LINE previews for human content review."""

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import run_line_model_phase_d as phase_d  # noqa: E402


PRIVATE_ENV = PROJECT_ROOT / ".env.line_bot"
BASE_URL = "http://127.0.0.1:8021/internal/line-model-benchmark/candidate-reply-preview"
SCENARIOS = (
    "fundamental_chip_technical_news",
    "technical_valuation_support_risk",
    "chip_night_us_events",
)
SOURCE_BINDINGS = (
    "review_src/core/line_model_contract.py",
    "review_src/core/line_model_validation.py",
    "review_src/services/line_model_shadow_service.py",
    "review_src/services/line_model_candidate_reply_service.py",
    "review_src/services/line_model_benchmark_service.py",
)


def _post(token: str, scenario: str) -> dict[str, Any]:
    started = time.monotonic()
    response = requests.post(
        BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"scenario": scenario, "code": "2330"},
        timeout=150,
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
        "finished_at": phase_d._now(),
        "body": body,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-attempts-per-scenario", type=int, default=10)
    parser.add_argument("--expected-driver", default="")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    raw_output = output.with_suffix(".jsonl")
    if output.exists() or raw_output.exists():
        raise FileExistsError("refusing to overwrite candidate preview evidence")
    output.parent.mkdir(parents=True, exist_ok=True)

    values = dotenv_values(PRIVATE_ENV)
    token = str(values.get("BOT_MARKET_DATA_TOKEN") or "")
    if len(token) < 32:
        raise RuntimeError("persistent BOT_MARKET_DATA_TOKEN is unavailable")
    os.environ.update({key: str(value) for key, value in values.items() if value is not None})
    deployment, model_profile, environment_match = phase_d._environment_snapshot(
        args.expected_driver
    )
    health_before = phase_d._service_health()
    attempts_by_scenario: dict[str, list[dict[str, Any]]] = {}
    selected_previews: dict[str, dict[str, Any]] = {}
    with raw_output.open("x", encoding="utf-8", newline="\n") as handle:
        for scenario in SCENARIOS:
            attempts: list[dict[str, Any]] = []
            for index in range(1, max(1, args.max_attempts_per_scenario) + 1):
                attempt = _post(token, scenario)
                attempt["attempt_index"] = index
                attempts.append(attempt)
                handle.write(json.dumps(attempt, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                body = attempt.get("body") if isinstance(attempt.get("body"), dict) else {}
                print(
                    json.dumps(
                        {
                            "scenario": scenario,
                            "attempt": index,
                            "http": attempt["http_status"],
                            "validator": body.get("validator_result"),
                            "reasons": body.get("validator_reason_codes"),
                            "preview_rendered": body.get("preview_rendered"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if attempt["http_status"] == 200 and body.get("preview_rendered") is True:
                    selected_previews[scenario] = {
                        "attempt_index": index,
                        "request_id": body.get("request_id"),
                        "preview": body.get("preview"),
                    }
                    break
            attempts_by_scenario[scenario] = attempts

    summaries: dict[str, Any] = {}
    for scenario, attempts in attempts_by_scenario.items():
        validator = Counter(
            str((attempt.get("body") or {}).get("validator_result") or "http_error")
            for attempt in attempts
        )
        reason_counts = Counter(
            reason
            for attempt in attempts
            for reason in list((attempt.get("body") or {}).get("validator_reason_codes") or [])
            if reason != "pass"
        )
        summaries[scenario] = {
            "attempt_count": len(attempts),
            "pass_count": validator.get("pass", 0),
            "reject_count": len(attempts) - validator.get("pass", 0),
            "reject_rate": round(
                (len(attempts) - validator.get("pass", 0)) / len(attempts), 4
            ),
            "validator_reason_counts": dict(sorted(reason_counts.items())),
            "selected_preview_attempt": (
                selected_previews.get(scenario, {}).get("attempt_index")
            ),
        }
    complete = len(selected_previews) == len(SCENARIOS)
    artifact = {
        "contract_version": "line-model-candidate-preview-audit-v1",
        "captured_at": phase_d._now(),
        "deployment_target": deployment,
        "environment_match": environment_match,
        "model_profile": model_profile,
        "source_hashes": {
            relative: phase_d._sha256(PROJECT_ROOT / relative) for relative in SOURCE_BINDINGS
        },
        "health_before": health_before,
        "health_after": phase_d._service_health(),
        "summary_by_scenario": summaries,
        "selected_previews": selected_previews,
        "all_three_scenarios_have_preview": complete,
        "raw_model_output_used_by_renderer": False,
        "candidate_reply_sent_to_line": False,
        "candidate_can_replace_reply": False,
        "valid_for_canary": False,
        "selection_bias_warning": (
            "Each selected preview must be reviewed together with attempt_count, "
            "reject_count, reject_rate, and validator_reason_counts in this artifact."
        ),
        "raw_attempts": raw_output.name,
    }
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"output": str(output), "all_three_scenarios_have_preview": complete},
            ensure_ascii=False,
        )
    )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
